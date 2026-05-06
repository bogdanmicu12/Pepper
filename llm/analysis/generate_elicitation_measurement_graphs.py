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

UES_ITEM_ALIASES = {
    "FA_S1": ("FA_S1", "FA-S.1", "FA-S1", "FAS1"),
    "FA_S2": ("FA_S2", "FA-S.2", "FA-S2", "FAS2"),
    "FA_S3": ("FA_S3", "FA-S.3", "FA-S3", "FAS3"),
    "PU_S1": ("PU_S1", "PU-S.1", "PU-S1", "PUS1"),
    "PU_S2": ("PU_S2", "PU-S.2", "PU-S2", "PUS2"),
    "PU_S3": ("PU_S3", "PU-S.3", "PU-S3", "PUS3"),
    "AE_S1": ("AE_S1", "AE-S.1", "AE-S1", "AES1"),
    "AE_S2": ("AE_S2", "AE-S.2", "AE-S2", "AES2"),
    "AE_S3": ("AE_S3", "AE-S.3", "AE-S3", "AES3"),
    "RW_S1": ("RW_S1", "RW-S.1", "RW-S1", "RWS1"),
    "RW_S2": ("RW_S2", "RW-S.2", "RW-S2", "RWS2"),
    "RW_S3": ("RW_S3", "RW-S.3", "RW-S3", "RWS3"),
}
UES_REVERSE_ITEMS = ("PU_S1", "PU_S2", "PU_S3")
UES_SUBSCALES = {
    "focused_attention": ("FA_S1", "FA_S2", "FA_S3"),
    "perceived_usability": ("PU_S1", "PU_S2", "PU_S3"),
    "aesthetic_appeal": ("AE_S1", "AE_S2", "AE_S3"),
    "reward": ("RW_S1", "RW_S2", "RW_S3"),
}


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


def normalize_ues_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rename_map: dict[str, str] = {}
    lower_lookup = {c.lower().replace(" ", "").replace(".", "").replace("-", "_"): c for c in df.columns}
    for canonical, aliases in UES_ITEM_ALIASES.items():
        for alias in aliases:
            key = alias.lower().replace(" ", "").replace(".", "").replace("-", "_")
            if key in lower_lookup:
                rename_map[lower_lookup[key]] = canonical
                break
    return df.rename(columns=rename_map)


def is_truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "elicitation"}


def detect_elicitation_rows(interventions: pd.DataFrame) -> pd.Series:
    if "is_elicitation" in interventions.columns:
        return interventions["is_elicitation"].map(is_truthy)
    if "prompt_type" in interventions.columns:
        prompt_type = interventions["prompt_type"].astype(str).str.strip().str.lower()
        explicit = prompt_type.eq("elicitation")
        if explicit.any():
            return explicit
    strategy = interventions["strategy"].astype(str).str.strip().str.lower()
    return ~strategy.isin(NON_ELICITATION_STRATEGIES)


def build_windows(interventions: pd.DataFrame, transcript: pd.DataFrame | None) -> pd.DataFrame:
    require_columns(interventions, ["session_id", "prompt_id", "phase", "strategy", "prompt_start_time", "prompt_end_time"], "intervention log")
    df = interventions.copy()
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
                }
            )
    return pd.DataFrame(windows)


def prepare_transcript(transcript: pd.DataFrame) -> pd.DataFrame:
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
        else:
            clipped_start = in_window["start_s"].clip(lower=float(window["window_start_s"]))
            clipped_end = in_window["end_s"].clip(upper=float(window["window_end_s"]))
            durations = (clipped_end - clipped_start).clip(lower=0.0)
            first_start = max(float(in_window["start_s"].min()), float(window["window_start_s"]))
            response_delay = max(0.0, first_start - float(window["window_start_s"]))
            speaking_time = float(durations.sum())
            turn_count = int(len(in_window))
            word_count = int(in_window["text"].astype(str).map(lambda t: len(re.findall(r"\b\w+\b", t))).sum())
            mean_turn_duration = float(durations.mean()) if len(durations) else np.nan
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
            }
        )
    return pd.DataFrame(metric_rows)


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


def score_ues(path: str | Path) -> pd.DataFrame:
    df = normalize_ues_columns(read_csv(path))
    missing = [item for item in UES_ITEM_ALIASES if item not in df.columns]
    if missing:
        raise ValueError(
            "UES-SF responses are missing item column(s): "
            + ", ".join(missing)
            + ". Use --write-templates to generate the accepted schema."
        )
    scored = df.copy()
    for item in UES_ITEM_ALIASES:
        scored[item] = pd.to_numeric(scored[item], errors="coerce")
        if scored[item].isna().any():
            raise ValueError(f"UES-SF item {item} contains missing or non-numeric values.")
        if not scored[item].between(1, 5).all():
            raise ValueError(f"UES-SF item {item} must use a 1-5 response scale.")
    for item in UES_REVERSE_ITEMS:
        scored[f"{item}_scored"] = 6 - scored[item]
    for item in set(UES_ITEM_ALIASES) - set(UES_REVERSE_ITEMS):
        scored[f"{item}_scored"] = scored[item]
    for subscale, items in UES_SUBSCALES.items():
        scored[subscale] = scored[[f"{item}_scored" for item in items]].mean(axis=1)
    scored["ues_sf_total"] = scored[[f"{item}_scored" for item in UES_ITEM_ALIASES]].mean(axis=1)
    return scored


def feedback_theme_counts(path: str | Path) -> pd.DataFrame:
    df = read_csv(path)
    require_columns(df, ["theme"], "open feedback codes")
    group_cols = [c for c in ["phase", "strategy", "question_id", "theme"] if c in df.columns]
    if "theme" not in group_cols:
        group_cols.append("theme")
    counts = df.groupby(group_cols, dropna=False).size().reset_index(name="count")
    return counts.sort_values("count", ascending=False)


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


def write_templates(output_dir: str | Path) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    templates = {
        "transcript_template.csv": [
            ["session_id", "speaker", "start_time", "end_time", "text"],
            ["S01", "P1", "2026-05-05T10:00:05", "2026-05-05T10:00:12", "Maybe we could use peer mentors."],
        ],
        "intervention_log_template.csv": [
            ["session_id", "prompt_id", "phase", "strategy", "prompt_type", "prompt_start_time", "prompt_end_time", "window_end_time"],
            ["S01", "E01", "divergence", "generative", "elicitation", "2026-05-05T10:00:00", "2026-05-05T10:00:04", ""],
            ["S01", "N01", "divergence", "", "normal", "2026-05-05T10:02:00", "2026-05-05T10:02:05", ""],
            ["S01", "E02", "divergence", "elaboration_evidence", "elicitation", "2026-05-05T10:04:00", "2026-05-05T10:04:07", ""],
        ],
        "manual_window_measures_template.csv": [
            ["window_id", "session_id", "prompt_id", "phase", "strategy", "idea_fluency", "elaboration_units", "elaborated_contribution_count", "consecutive_topic_turns", "coder_id", "notes"],
            ["S01__E01", "S01", "E01", "divergence", "generative", "3", "5", "2", "4", "coder_A", "Manual coding after transcript review"],
        ],
        "ues_sf_responses_template.csv": [
            ["participant_id", "session_id", "phase", "strategy", *UES_ITEM_ALIASES.keys()],
            ["P01", "S01", "post_session", "overall", "4", "3", "4", "2", "1", "2", "4", "5", "4", "5", "4", "5"],
        ],
        "open_feedback_coded_template.csv": [
            ["participant_id", "session_id", "phase", "strategy", "question_id", "theme", "valence", "excerpt_or_note"],
            ["P01", "S01", "post_session", "generative", "most_useful", "helped_generate_more_options", "positive", "Short paraphrase or coded note"],
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

    if args.ues_responses:
        ues = score_ues(args.ues_responses)
        ues.to_csv(output_dir / "ues_sf_scores.csv", index=False)
        ues_summary = summarize_numeric(
            ues,
            ["focused_attention", "perceived_usability", "aesthetic_appeal", "reward", "ues_sf_total"],
            [c for c in ["phase", "strategy"] if c in ues.columns] or ["session_id"],
        )
        ues_summary.to_csv(output_dir / "ues_sf_summary.csv", index=False)
        chart_group_means(ues, "ues_sf_total", "Mean UES-SF Total Score", "1-5 scored average", chart_dir / "ues_sf_total.png")

    if args.feedback_codes:
        counts = feedback_theme_counts(args.feedback_codes)
        counts.to_csv(output_dir / "open_feedback_theme_counts.csv", index=False)
        top = counts.head(args.max_feedback_themes).copy()
        if not top.empty:
            label_cols = [c for c in ["phase", "strategy", "theme"] if c in top.columns]
            labels = top[label_cols].astype(str).agg(" / ".join, axis=1).tolist()
            draw_bar_chart(
                ChartSeries(
                    labels=labels,
                    values=top["count"].astype(float).tolist(),
                    title="Most Frequent Open Feedback Themes",
                    y_label="Count",
                    output_path=chart_dir / "open_feedback_theme_counts.png",
                )
            )

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
    parser.add_argument("--ues-responses", help="CSV with UES-SF 1-5 item responses.")
    parser.add_argument("--feedback-codes", help="CSV with manually coded open-feedback themes.")
    parser.add_argument("--output-dir", default="llm/analysis/outputs", help="Directory for CSV summaries and PNG charts.")
    parser.add_argument("--write-templates", help="Write input CSV templates to this directory and exit.")
    parser.add_argument("--participant-speakers", nargs="*", default=list(PARTICIPANT_DEFAULTS), help="Speaker labels treated as participants.")
    parser.add_argument("--robot-speakers", nargs="*", default=list(ROBOT_DEFAULTS), help="Speaker labels treated as robot/facilitator.")
    parser.add_argument("--max-feedback-themes", type=int, default=12, help="Maximum feedback themes to show in chart.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.write_templates:
        write_templates(args.write_templates)
        print(f"Wrote templates to {Path(args.write_templates).resolve()}")
        return 0
    if not any([args.transcript, args.interventions, args.manual_measures, args.ues_responses, args.feedback_codes]):
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
