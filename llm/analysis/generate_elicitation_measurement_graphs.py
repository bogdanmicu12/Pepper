from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PARTICIPANT_DEFAULTS = ("P1", "P2", "Participant", "participant", "Human")
ROBOT_DEFAULTS = ("Robot", "Pepper", "Facilitator", "robot", "pepper")
NON_ELICITATION_STRATEGIES = {"", "none", "nan", "context_only", "normal", "baseline"}
ENGAGEMENT_SCORE_ALIASES = (
    "elicitation_engagement_score",
    "evaluation_elicitation_score",
    "engagement_score",
    "engagement_1_100",
)
BACKCHANNEL_RE = re.compile(
    r"\b(?:yeah|yep|yes|ja|mhm|mmhm|mm-hm|uh-huh|uh huh|right|okay|ok|exactly|true|sure|nice|cool|fair)\b",
    re.IGNORECASE,
)
LAUGHTER_RE = re.compile(r"\b(?:ha+ha+|hehe+|lol|laughs?|laughing)\b", re.IGNORECASE)
FILLER_RE = re.compile(r"\b(?:uh|um|erm|ehm|eh|like)\b", re.IGNORECASE)
LONG_PAUSE_SECONDS = 3.0
ADJACENCY_RESPONSE_SECONDS = 8.0


@dataclass
class ChartSeries:
    labels: list[str]
    values: list[float]
    title: str
    y_label: str
    output_path: Path


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def read_csv(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    return _clean_columns(pd.read_csv(path))


def require_columns(df: pd.DataFrame, required: Sequence[str], label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"{label} is missing required column(s): {joined}")


def _parse_clock_time(value: str) -> float | None:
    if pd.isna(value):
        return np.nan
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return np.nan
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?", text):
        parts = [float(p) for p in text.split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    return None


def parse_time_column(series: pd.Series, column_name: str) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().sum() == len(series.dropna()):
        return numeric.astype(float)

    parsed_clock = series.astype(str).map(_parse_clock_time)
    parsed_clock = pd.to_numeric(parsed_clock, errors="coerce")
    if parsed_clock.notna().sum() == len(series.dropna()):
        return parsed_clock.astype(float)

    parsed_dt = pd.to_datetime(series, errors="coerce")
    if parsed_dt.notna().sum() == len(series.dropna()):
        return parsed_dt.map(lambda value: value.timestamp() if pd.notna(value) else np.nan).astype(float)

    bad_examples = series[parsed_dt.isna() & numeric.isna()].dropna().head(3).tolist()
    raise ValueError(
        f"Could not parse time column '{column_name}'. Use seconds, ISO datetimes, "
        f"or mm:ss/hh:mm:ss values. Examples that failed: {bad_examples}"
    )


def is_truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "elicitation"}


def first_existing_column(df: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    lookup = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in lookup:
            return lookup[key]
    return None


def normalize_intervention_columns(interventions: pd.DataFrame) -> pd.DataFrame:
    df = interventions.copy()
    if "prompt_start_time" not in df.columns and "start_timestamp" in df.columns:
        df["prompt_start_time"] = df["start_timestamp"]
    if "prompt_end_time" not in df.columns and "end_timestamp" in df.columns:
        df["prompt_end_time"] = df["end_timestamp"]
    return df


def normalize_transcript_columns(transcript: pd.DataFrame) -> pd.DataFrame:
    df = transcript.copy()
    if "start_time" not in df.columns and "start_timestamp" in df.columns:
        df["start_time"] = df["start_timestamp"]
    if "end_time" not in df.columns and "end_timestamp" in df.columns:
        df["end_time"] = df["end_timestamp"]
    return df


def normalize_engagement_score(value: object) -> float:
    score = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(score) or float(score) < 1 or float(score) > 100:
        return np.nan
    return float(score)


def count_words(text: object) -> int:
    return len(re.findall(r"\b\w+\b", str(text)))


def count_pattern(series: pd.Series, pattern: re.Pattern[str]) -> int:
    return int(series.astype(str).map(lambda text: len(pattern.findall(text))).sum())


def count_short_backchannels(series: pd.Series) -> int:
    count = 0
    for text in series.astype(str):
        words = re.findall(r"\b\w+\b", text.lower())
        if 1 <= len(words) <= 5 and BACKCHANNEL_RE.search(text):
            count += 1
    return count


def compute_overlap_count(turns: pd.DataFrame) -> int:
    if len(turns) < 2:
        return 0
    ordered = turns.sort_values(["start_s", "end_s"])
    overlaps = 0
    previous_end = -np.inf
    previous_speaker = ""
    for _, row in ordered.iterrows():
        speaker = str(row.get("speaker", "")).strip().lower()
        start = float(row["start_s"])
        end = float(row["end_s"])
        if start < previous_end and speaker and speaker != previous_speaker:
            overlaps += 1
        if end > previous_end:
            previous_end = end
            previous_speaker = speaker
    return overlaps


def compute_long_pause_seconds(turns: pd.DataFrame, window_start: float, window_end: float) -> float:
    if window_end <= window_start:
        return 0.0
    if turns.empty:
        return max(0.0, window_end - window_start - LONG_PAUSE_SECONDS)
    ordered = turns.sort_values(["start_s", "end_s"])
    pause_seconds = 0.0
    previous_end = window_start
    for _, row in ordered.iterrows():
        start = max(float(row["start_s"]), window_start)
        gap = max(0.0, start - previous_end)
        if gap > LONG_PAUSE_SECONDS:
            pause_seconds += gap - LONG_PAUSE_SECONDS
        previous_end = max(previous_end, min(float(row["end_s"]), window_end))
    return pause_seconds


def zscore(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    std = numeric.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series([0.0] * len(series), index=series.index)
    return (numeric - numeric.mean()) / std


def clipped_score(series: pd.Series) -> pd.Series:
    return series.clip(lower=0.0, upper=100.0).round(2)


def detect_elicitation_rows(interventions: pd.DataFrame) -> pd.Series:
    event_filter = pd.Series([True] * len(interventions), index=interventions.index)
    if "event_type" in interventions.columns:
        event_filter = interventions["event_type"].astype(str).str.strip().str.lower().eq("robot")

    if "is_elicitation" in interventions.columns:
        return interventions["is_elicitation"].map(is_truthy) & event_filter
    if "prompt_type" in interventions.columns:
        prompt_type = interventions["prompt_type"].astype(str).str.strip().str.lower()
        explicit = prompt_type.eq("elicitation")
        if explicit.any():
            return explicit & event_filter
    strategy = interventions["strategy"].astype(str).str.strip().str.lower()
    return ~strategy.isin(NON_ELICITATION_STRATEGIES) & event_filter


def build_windows(interventions: pd.DataFrame, transcript: pd.DataFrame | None) -> pd.DataFrame:
    df = normalize_intervention_columns(interventions)
    require_columns(df, ["session_id", "prompt_id", "phase", "strategy", "prompt_start_time", "prompt_end_time"], "intervention log")
    df["prompt_start_s"] = parse_time_column(df["prompt_start_time"], "prompt_start_time")
    df["prompt_end_s"] = parse_time_column(df["prompt_end_time"], "prompt_end_time")
    df["_is_elicitation"] = detect_elicitation_rows(df)
    elic = df[df["_is_elicitation"]].copy()
    if elic.empty:
        raise ValueError("No elicitation rows found in the intervention log.")

    if "window_end_time" in elic.columns:
        explicit_end = parse_time_column(elic["window_end_time"], "window_end_time")
    else:
        explicit_end = pd.Series([np.nan] * len(elic), index=elic.index, dtype=float)

    transcript_end_by_session: dict[str, float] = {}
    if transcript is not None and not transcript.empty:
        transcript_end_by_session = transcript.groupby("session_id")["end_s"].max().to_dict()

    score_col = first_existing_column(df, ENGAGEMENT_SCORE_ALIASES)
    direct_scores: dict[tuple[str, str], float] = {}
    previous_scores: dict[tuple[str, str], float] = {}
    if score_col:
        for _, score_row in df.iterrows():
            score = normalize_engagement_score(score_row.get(score_col))
            if pd.isna(score):
                continue
            session_key = str(score_row.get("session_id", ""))
            previous_prompt = str(score_row.get("previous_elicitation_prompt_id", "")).strip()
            if previous_prompt.lower() in {"nan", "none"}:
                previous_prompt = ""
            if previous_prompt:
                previous_scores[(session_key, previous_prompt)] = score
                continue
            prompt_key = str(score_row.get("prompt_id", "")).strip()
            if prompt_key.lower() in {"nan", "none"}:
                prompt_key = ""
            if prompt_key:
                direct_scores[(session_key, prompt_key)] = score

    windows: list[dict[str, object]] = []
    for session_id, session_rows in elic.sort_values(["session_id", "prompt_start_s"]).groupby("session_id", sort=False):
        rows = session_rows.reset_index()
        for i, row in rows.iterrows():
            next_start = np.nan
            if i + 1 < len(rows):
                next_start = float(rows.loc[i + 1, "prompt_start_s"])
            fallback_end = transcript_end_by_session.get(str(session_id), np.nan)
            candidates = [
                float(row["prompt_end_s"]),
                next_start,
                float(explicit_end.loc[row["index"]]) if pd.notna(explicit_end.loc[row["index"]]) else np.nan,
                fallback_end,
            ]
            usable = [c for c in candidates[1:] if pd.notna(c) and c > float(row["prompt_end_s"])]
            window_end = min(usable) if usable else float(row["prompt_end_s"])
            windows.append(
                {
                    "window_id": f"{session_id}__{row['prompt_id']}",
                    "session_id": session_id,
                    "prompt_id": row["prompt_id"],
                    "phase": row["phase"],
                    "strategy": row["strategy"],
                    "window_start_s": float(row["prompt_end_s"]),
                    "window_end_s": float(window_end),
                    "window_duration_seconds": max(0.0, float(window_end) - float(row["prompt_end_s"])),
                    "elicitation_engagement_score": direct_scores.get(
                        (str(session_id), str(row["prompt_id"]).strip()),
                        np.nan,
                    ),
                }
            )
    window_df = pd.DataFrame(windows)
    if previous_scores and not window_df.empty:
        for (session_key, prompt_key), score in previous_scores.items():
            mask = (
                window_df["session_id"].astype(str).eq(session_key)
                & window_df["prompt_id"].astype(str).str.strip().eq(prompt_key)
            )
            needs_score = mask & window_df["elicitation_engagement_score"].isna()
            window_df.loc[needs_score, "elicitation_engagement_score"] = score
    return window_df


def prepare_transcript(transcript: pd.DataFrame) -> pd.DataFrame:
    transcript = normalize_transcript_columns(transcript)
    require_columns(transcript, ["session_id", "speaker", "start_time"], "transcript")
    df = transcript.copy()
    df["start_s"] = parse_time_column(df["start_time"], "start_time")
    if "end_time" in df.columns:
        df["end_s"] = parse_time_column(df["end_time"], "end_time")
    elif "duration_seconds" in df.columns:
        duration = pd.to_numeric(df["duration_seconds"], errors="coerce")
        if duration.isna().any():
            raise ValueError("duration_seconds contains non-numeric values.")
        df["end_s"] = df["start_s"] + duration
    else:
        raise ValueError("transcript must include either end_time or duration_seconds.")
    if "text" not in df.columns:
        df["text"] = ""
    df["duration_seconds"] = (df["end_s"] - df["start_s"]).clip(lower=0.0)
    return df


def compute_transcript_metrics(
    windows: pd.DataFrame,
    transcript: pd.DataFrame,
    participant_speakers: Sequence[str],
    robot_speakers: Sequence[str],
) -> pd.DataFrame:
    participant_set = {s.strip().lower() for s in participant_speakers}
    robot_set = {s.strip().lower() for s in robot_speakers}

    def is_participant_speaker(speaker: object) -> bool:
        normalized = str(speaker).strip().lower()
        if normalized in participant_set or any(normalized.startswith(p) for p in participant_set if p):
            return True
        if normalized in robot_set:
            return False
        return True

    transcript = transcript.copy()
    transcript["_participant"] = transcript["speaker"].map(is_participant_speaker)
    participant_turns = transcript[transcript["_participant"]].copy()

    metric_rows: list[dict[str, object]] = []
    for _, window in windows.iterrows():
        session_turns = participant_turns[participant_turns["session_id"].astype(str) == str(window["session_id"])].copy()
        in_window = session_turns[
            (session_turns["end_s"] > window["window_start_s"]) &
            (session_turns["start_s"] < window["window_end_s"])
        ].copy()
        if in_window.empty:
            response_delay = np.nan
            speaking_time = 0.0
            turn_count = 0
            word_count = 0
            mean_turn_duration = np.nan
            mean_audio_rms = np.nan
            words_per_second = np.nan
            long_pause_seconds = max(0.0, float(window["window_duration_seconds"]) - LONG_PAUSE_SECONDS)
            long_pause_seconds_per_minute = (
                long_pause_seconds / max(float(window["window_duration_seconds"]) / 60.0, 1e-9)
                if float(window["window_duration_seconds"]) > 0 else np.nan
            )
            backchannel_count = 0
            laughter_count = 0
            filler_count = 0
            cooperative_overlap_count = 0
            adjacency_response_success = 0
            connection_cues_per_minute = 0.0
        else:
            clipped_start = in_window["start_s"].clip(lower=float(window["window_start_s"]))
            clipped_end = in_window["end_s"].clip(upper=float(window["window_end_s"]))
            durations = (clipped_end - clipped_start).clip(lower=0.0)
            first_start = max(float(in_window["start_s"].min()), float(window["window_start_s"]))
            response_delay = max(0.0, first_start - float(window["window_start_s"]))
            speaking_time = float(durations.sum())
            turn_count = int(len(in_window))
            word_count = int(in_window["text"].astype(str).map(count_words).sum())
            mean_turn_duration = float(durations.mean()) if len(durations) else np.nan
            audio_rms = pd.to_numeric(in_window.get("audio_rms", pd.Series([], dtype=float)), errors="coerce")
            mean_audio_rms = float(audio_rms.mean()) if audio_rms.notna().any() else np.nan
            words_per_second = word_count / speaking_time if speaking_time > 0 else np.nan
            long_pause_seconds = compute_long_pause_seconds(
                in_window,
                float(window["window_start_s"]),
                float(window["window_end_s"]),
            )
            window_minutes = max(float(window["window_duration_seconds"]) / 60.0, 1e-9)
            long_pause_seconds_per_minute = long_pause_seconds / window_minutes
            backchannel_count = count_short_backchannels(in_window["text"])
            laughter_count = count_pattern(in_window["text"], LAUGHTER_RE)
            filler_count = count_pattern(in_window["text"], FILLER_RE)
            cooperative_overlap_count = compute_overlap_count(in_window)
            adjacency_response_success = int(pd.notna(response_delay) and response_delay <= ADJACENCY_RESPONSE_SECONDS and word_count > 0)
            connection_cue_count = backchannel_count + laughter_count + cooperative_overlap_count + adjacency_response_success
            connection_cues_per_minute = connection_cue_count / window_minutes
        metric_rows.append(
            {
                "window_id": window["window_id"],
                "session_id": window["session_id"],
                "prompt_id": window["prompt_id"],
                "phase": window["phase"],
                "strategy": window["strategy"],
                "response_delay_seconds": response_delay,
                "participant_speaking_time_seconds": speaking_time,
                "participant_turn_count": turn_count,
                "participant_word_count": word_count,
                "mean_participant_turn_duration_seconds": mean_turn_duration,
                "mean_audio_rms": mean_audio_rms,
                "participant_words_per_second": words_per_second,
                "long_pause_seconds": long_pause_seconds,
                "long_pause_seconds_per_minute": long_pause_seconds_per_minute,
                "backchannel_count": backchannel_count,
                "laughter_count": laughter_count,
                "filler_count": filler_count,
                "cooperative_overlap_count": cooperative_overlap_count,
                "adjacency_response_success": adjacency_response_success,
                "connection_cues_per_minute": connection_cues_per_minute,
            }
        )
    metrics = pd.DataFrame(metric_rows)
    if metrics.empty:
        return metrics

    log_rms = np.log1p(pd.to_numeric(metrics["mean_audio_rms"], errors="coerce"))
    activation_raw = (
        zscore(log_rms).fillna(0.0)
        + zscore(metrics["participant_words_per_second"]).fillna(0.0)
        - zscore(metrics["long_pause_seconds_per_minute"]).fillna(0.0)
    ) / 3.0
    metrics["vocal_activation_score"] = clipped_score(50.0 + 15.0 * activation_raw)

    window_minutes = (pd.to_numeric(metrics["participant_speaking_time_seconds"], errors="coerce") / 60.0).replace(0, np.nan)
    backchannels_per_speaking_minute = (
        pd.to_numeric(metrics["backchannel_count"], errors="coerce") / window_minutes
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    laughter_per_speaking_minute = (
        pd.to_numeric(metrics["laughter_count"], errors="coerce") / window_minutes
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    overlap_per_window = pd.to_numeric(metrics["cooperative_overlap_count"], errors="coerce").fillna(0.0)
    metrics["connection_cue_score"] = clipped_score(
        25.0 * pd.to_numeric(metrics["adjacency_response_success"], errors="coerce").fillna(0.0)
        + 30.0 * np.minimum(backchannels_per_speaking_minute / 2.0, 1.0)
        + 20.0 * np.minimum(laughter_per_speaking_minute / 1.0, 1.0)
        + 15.0 * np.minimum(overlap_per_window / 2.0, 1.0)
        + 10.0 * np.minimum(pd.to_numeric(metrics["connection_cues_per_minute"], errors="coerce").fillna(0.0) / 3.0, 1.0)
    )
    return metrics


def summarize_numeric(df: pd.DataFrame, metrics: Sequence[str], group_cols: Sequence[str]) -> pd.DataFrame:
    present = [m for m in metrics if m in df.columns]
    if not present:
        return pd.DataFrame()
    grouped = df.groupby(list(group_cols), dropna=False)[present]
    summary = grouped.agg(["count", "mean", "median", "std"]).reset_index()
    summary.columns = ["_".join([str(part) for part in col if part]) for col in summary.columns.to_flat_index()]
    return summary


def load_manual_measures(path: str | Path) -> pd.DataFrame:
    df = read_csv(path)
    require_columns(df, ["phase", "strategy"], "manual measures")
    numeric_cols = [
        "idea_fluency",
        "elaboration_units",
        "elaborated_contribution_count",
        "consecutive_topic_turns",
        "topic_chain_turns",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "consecutive_topic_turns" not in df.columns and "topic_chain_turns" in df.columns:
        df["consecutive_topic_turns"] = df["topic_chain_turns"]
    if "elaboration_units" in df.columns and "idea_fluency" in df.columns:
        df["elaboration_units_per_idea"] = np.where(
            df["idea_fluency"] > 0,
            df["elaboration_units"] / df["idea_fluency"],
            np.nan,
        )
    return df


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "Calibri.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = str(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def draw_bar_chart(series: ChartSeries) -> None:
    labels = series.labels
    values = [0.0 if pd.isna(v) else float(v) for v in series.values]
    if not labels:
        return

    width, height = 1400, 900
    margin_left, margin_right, margin_top, margin_bottom = 130, 60, 95, 190
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(34, bold=True)
    label_font = _font(21)
    small_font = _font(18)
    axis_font = _font(20, bold=True)

    palette = ["#3465a4", "#4e9a06", "#c17d11", "#75507b", "#cc0000", "#2e3436"]
    title_w, _ = _text_size(draw, series.title, title_font)
    draw.text(((width - title_w) / 2, 28), series.title, fill="#1f2933", font=title_font)

    x0, y0 = margin_left, margin_top
    x1, y1 = width - margin_right, height - margin_bottom
    draw.line([(x0, y1), (x1, y1)], fill="#2f3a45", width=3)
    draw.line([(x0, y0), (x0, y1)], fill="#2f3a45", width=3)

    max_value = max(values) if values else 1.0
    if max_value <= 0:
        max_value = 1.0
    y_max = max_value * 1.15
    tick_count = 5
    for i in range(tick_count + 1):
        frac = i / tick_count
        y = y1 - frac * plot_h
        val = y_max * frac
        draw.line([(x0 - 8, y), (x0, y)], fill="#2f3a45", width=2)
        draw.line([(x0, y), (x1, y)], fill="#e6e8eb", width=1)
        tick = f"{val:.1f}" if y_max < 20 else f"{val:.0f}"
        tw, th = _text_size(draw, tick, small_font)
        draw.text((x0 - tw - 14, y - th / 2), tick, fill="#374151", font=small_font)

    n = len(labels)
    gap = max(8, int(plot_w * 0.02))
    bar_slot = plot_w / n
    bar_w = max(22, min(90, int(bar_slot * 0.56)))
    for i, (label, value) in enumerate(zip(labels, values)):
        center = x0 + bar_slot * (i + 0.5)
        bar_x0 = center - bar_w / 2
        bar_x1 = center + bar_w / 2
        bar_h = 0 if y_max == 0 else (value / y_max) * plot_h
        bar_y0 = y1 - bar_h
        color = palette[i % len(palette)]
        draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, y1], radius=5, fill=color)
        value_text = f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"
        vw, vh = _text_size(draw, value_text, small_font)
        draw.text((center - vw / 2, max(y0, bar_y0 - vh - 8)), value_text, fill="#111827", font=small_font)

        wrapped = wrap_text(draw, label, label_font, int(bar_slot - gap))
        ty = y1 + 18
        for line in wrapped[:4]:
            lw, lh = _text_size(draw, line, label_font)
            draw.text((center - lw / 2, ty), line, fill="#111827", font=label_font)
            ty += lh + 4

    yw, yh = _text_size(draw, series.y_label, axis_font)
    ylabel_img = Image.new("RGBA", (yw + 12, yh + 12), (255, 255, 255, 0))
    ydraw = ImageDraw.Draw(ylabel_img)
    ydraw.text((6, 6), series.y_label, fill="#1f2933", font=axis_font)
    rotated = ylabel_img.rotate(90, expand=True)
    image.paste(rotated, (22, int(y0 + plot_h / 2 - rotated.height / 2)), rotated)

    series.output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(series.output_path)


def chart_group_means(df: pd.DataFrame, metric: str, title: str, y_label: str, output_path: Path) -> None:
    if metric not in df.columns:
        return
    work = df.copy()
    work[metric] = pd.to_numeric(work[metric], errors="coerce")
    group_cols = [c for c in ["phase", "strategy"] if c in work.columns]
    if not group_cols:
        group_cols = ["session_id"] if "session_id" in work.columns else []
    if not group_cols:
        return
    grouped = work.groupby(group_cols, dropna=False)[metric].mean().reset_index()
    grouped = grouped.dropna(subset=[metric])
    if grouped.empty:
        return
    labels = grouped[group_cols].astype(str).agg(" / ".join, axis=1).tolist()
    values = grouped[metric].astype(float).tolist()
    draw_bar_chart(ChartSeries(labels, values, title, y_label, output_path))


def draw_dashboard(chart_dir: Path, output_path: Path) -> None:
    preferred = [
        "elicitation_engagement_by_phase_strategy.png",
        "vocal_activation_by_phase_strategy.png",
        "connection_cue_score_by_phase_strategy.png",
        "response_delay_by_phase_strategy.png",
        "speaking_time_by_phase_strategy.png",
        "speech_rate_by_phase_strategy.png",
        "long_pause_burden_by_phase_strategy.png",
        "connection_cues_per_minute_by_phase_strategy.png",
        "idea_fluency_by_phase_strategy.png",
        "elaboration_units_by_phase_strategy.png",
        "elaboration_units_per_idea_by_phase_strategy.png",
        "consecutive_topic_turns_by_phase_strategy.png",
    ]
    chart_paths = [chart_dir / name for name in preferred if (chart_dir / name).exists()]
    if not chart_paths:
        return

    thumb_w, thumb_h = 700, 450
    cols = 2
    rows = int(np.ceil(len(chart_paths) / cols))
    title_h = 70
    image = Image.new("RGB", (cols * thumb_w, title_h + rows * thumb_h), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(34, bold=True)
    title = "Pepper Elicitation Measurement Dashboard"
    tw, th = _text_size(draw, title, title_font)
    draw.text(((image.width - tw) / 2, 20), title, fill="#111827", font=title_font)

    for idx, path in enumerate(chart_paths):
        with Image.open(path) as chart:
            chart = chart.convert("RGB")
            chart.thumbnail((thumb_w - 20, thumb_h - 20), Image.LANCZOS)
            col = idx % cols
            row = idx // cols
            x = col * thumb_w + (thumb_w - chart.width) // 2
            y = title_h + row * thumb_h + (thumb_h - chart.height) // 2
            image.paste(chart, (x, y))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def write_templates(output_dir: str | Path) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    templates = {
        "transcript_template.csv": [
            ["session_id", "speaker", "start_time", "end_time", "text"],
            ["S01", "P1", "2026-05-05T10:00:05", "2026-05-05T10:00:12", "Maybe we could use peer mentors."],
        ],
        "intervention_log_template.csv": [
            ["session_id", "prompt_id", "phase", "strategy", "prompt_type", "prompt_start_time", "prompt_end_time", "window_end_time", "elicitation_engagement_score"],
            ["S01", "E01", "divergence", "generative", "elicitation", "2026-05-05T10:00:00", "2026-05-05T10:00:04", "", "72"],
            ["S01", "N01", "divergence", "", "normal", "2026-05-05T10:02:00", "2026-05-05T10:02:05", "", ""],
            ["S01", "E02", "divergence", "elaboration_evidence", "elicitation", "2026-05-05T10:04:00", "2026-05-05T10:04:07", "", "81"],
        ],
        "manual_window_measures_template.csv": [
            ["window_id", "session_id", "prompt_id", "phase", "strategy", "idea_fluency", "elaboration_units", "elaborated_contribution_count", "consecutive_topic_turns", "coder_id", "notes"],
            ["S01__E01", "S01", "E01", "divergence", "generative", "3", "5", "2", "4", "coder_A", "Manual coding after transcript review"],
        ],
    }
    for filename, rows in templates.items():
        with (out / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerows(rows)


def save_outputs(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_dir = output_dir / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)

    transcript = None
    windows = None
    combined_rows: list[pd.DataFrame] = []
    summary_rows: list[pd.DataFrame] = []

    if args.transcript:
        transcript = prepare_transcript(read_csv(args.transcript))

    if args.interventions:
        windows = build_windows(read_csv(args.interventions), transcript)
        windows.to_csv(output_dir / "elicitation_windows.csv", index=False)
        if "elicitation_engagement_score" in windows.columns and windows["elicitation_engagement_score"].notna().any():
            engagement_summary = summarize_numeric(
                windows,
                ["elicitation_engagement_score"],
                ["phase", "strategy"],
            )
            engagement_summary.to_csv(output_dir / "elicitation_engagement_summary_by_phase_strategy.csv", index=False)
            summary_rows.append(engagement_summary)
            chart_group_means(
                windows,
                "elicitation_engagement_score",
                "Mean Elicitation Engagement Score",
                "1-100 score",
                chart_dir / "elicitation_engagement_by_phase_strategy.png",
            )

    if transcript is not None and windows is not None:
        transcript_metrics = compute_transcript_metrics(
            windows,
            transcript,
            participant_speakers=args.participant_speakers,
            robot_speakers=args.robot_speakers,
        )
        transcript_metrics.to_csv(output_dir / "transcript_window_metrics.csv", index=False)
        combined_rows.append(transcript_metrics)
        transcript_summary = summarize_numeric(
            transcript_metrics,
            [
                "response_delay_seconds",
                "participant_speaking_time_seconds",
                "participant_turn_count",
                "participant_word_count",
                "mean_participant_turn_duration_seconds",
                "mean_audio_rms",
                "participant_words_per_second",
                "long_pause_seconds_per_minute",
                "backchannel_count",
                "laughter_count",
                "cooperative_overlap_count",
                "adjacency_response_success",
                "connection_cues_per_minute",
                "vocal_activation_score",
                "connection_cue_score",
            ],
            ["phase", "strategy"],
        )
        transcript_summary.to_csv(output_dir / "transcript_summary_by_phase_strategy.csv", index=False)
        summary_rows.append(transcript_summary)
        chart_group_means(
            transcript_metrics,
            "response_delay_seconds",
            "Mean Response Delay by Phase and Strategy",
            "Seconds",
            chart_dir / "response_delay_by_phase_strategy.png",
        )
        chart_group_means(
            transcript_metrics,
            "participant_speaking_time_seconds",
            "Mean Participant Speaking Time Until Next Elicitation",
            "Seconds",
            chart_dir / "speaking_time_by_phase_strategy.png",
        )
        chart_group_means(
            transcript_metrics,
            "vocal_activation_score",
            "Mean Vocal Activation Score",
            "0-100 index",
            chart_dir / "vocal_activation_by_phase_strategy.png",
        )
        chart_group_means(
            transcript_metrics,
            "connection_cue_score",
            "Mean Connection Cue Score",
            "0-100 index",
            chart_dir / "connection_cue_score_by_phase_strategy.png",
        )
        chart_group_means(
            transcript_metrics,
            "participant_words_per_second",
            "Mean Participant Speech Rate",
            "Words per second",
            chart_dir / "speech_rate_by_phase_strategy.png",
        )
        chart_group_means(
            transcript_metrics,
            "long_pause_seconds_per_minute",
            "Mean Long-Pause Burden",
            "Seconds per minute",
            chart_dir / "long_pause_burden_by_phase_strategy.png",
        )
        chart_group_means(
            transcript_metrics,
            "connection_cues_per_minute",
            "Mean Connection Cues per Minute",
            "Cues per minute",
            chart_dir / "connection_cues_per_minute_by_phase_strategy.png",
        )

    if args.manual_measures:
        manual = load_manual_measures(args.manual_measures)
        manual.to_csv(output_dir / "manual_window_measures_cleaned.csv", index=False)
        manual_summary = summarize_numeric(
            manual,
            [
                "idea_fluency",
                "elaboration_units",
                "elaboration_units_per_idea",
                "elaborated_contribution_count",
                "consecutive_topic_turns",
            ],
            ["phase", "strategy"],
        )
        manual_summary.to_csv(output_dir / "manual_summary_by_phase_strategy.csv", index=False)
        summary_rows.append(manual_summary)
        chart_group_means(manual, "idea_fluency", "Mean Idea Fluency", "Distinct ideas", chart_dir / "idea_fluency_by_phase_strategy.png")
        chart_group_means(manual, "elaboration_units", "Mean Elaboration Units", "Elaborative units", chart_dir / "elaboration_units_by_phase_strategy.png")
        chart_group_means(
            manual,
            "elaboration_units_per_idea",
            "Mean Elaboration Units per Idea",
            "Units per idea",
            chart_dir / "elaboration_units_per_idea_by_phase_strategy.png",
        )
        chart_group_means(
            manual,
            "consecutive_topic_turns",
            "Mean Consecutive Turns on Same Subject",
            "Turns",
            chart_dir / "consecutive_topic_turns_by_phase_strategy.png",
        )

    draw_dashboard(chart_dir, output_dir / "pepper_measurement_dashboard.png")

    manifest = {
        "output_dir": str(output_dir.resolve()),
        "charts_dir": str(chart_dir.resolve()),
        "generated_files": sorted(str(p.relative_to(output_dir)) for p in output_dir.rglob("*") if p.is_file()),
    }
    pd.Series(manifest, dtype="object").to_json(output_dir / "run_manifest.json", indent=2)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Pepper elicitation measurement graphs and summaries.")
    parser.add_argument("--transcript", help="CSV transcript with session_id, speaker, start_time, end_time/duration_seconds, text.")
    parser.add_argument("--interventions", help="CSV robot/intervention log defining elicitation prompts.")
    parser.add_argument("--manual-measures", help="CSV with manually coded idea fluency, elaboration, and topic-chain measures.")
    parser.add_argument("--output-dir", default="llm/analysis/outputs", help="Directory for CSV summaries and PNG charts.")
    parser.add_argument("--write-templates", help="Write input CSV templates to this directory and exit.")
    parser.add_argument("--participant-speakers", nargs="*", default=list(PARTICIPANT_DEFAULTS), help="Speaker labels treated as participants.")
    parser.add_argument("--robot-speakers", nargs="*", default=list(ROBOT_DEFAULTS), help="Speaker labels treated as robot/facilitator.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.write_templates:
        write_templates(args.write_templates)
        print(f"Wrote templates to {Path(args.write_templates).resolve()}")
        return 0
    if not any([args.transcript, args.interventions, args.manual_measures]):
        raise SystemExit("No inputs provided. Use --write-templates to create CSV templates, or pass one or more input CSV files.")
    if args.transcript and not args.interventions:
        raise SystemExit("--transcript requires --interventions so elicitation windows can be constructed.")
    if args.interventions and not args.transcript:
        print("Warning: --interventions was provided without --transcript; only windows will be written.", file=sys.stderr)
    save_outputs(args)
    print(f"Wrote outputs to {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
