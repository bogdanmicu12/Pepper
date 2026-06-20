from __future__ import annotations

import argparse
import csv
import math
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
PHASE_SORT_ORDER = {"divergence": 0, "convergence": 1}
STRATEGY_SORT_ORDER = {
    "generative": 0,
    "elaboration_evidence": 1,
    "elaborative": 1,
    "perspective_shift": 2,
    "perspective": 2,
}
EVALUATION_MOMENT_SORT_ORDER = {
    "start": 0,
    "after_previous_elicitation": 1,
    "direct_window": 2,
    "end": 3,
    "unspecified": 99,
}
ENGAGEMENT_SCORE_ALIASES = (
    "elicitation_engagement_score",
    "evaluation_elicitation_score",
    "engagement_score",
    "engagement_1_100",
)
CREATIVE_CONFIDENCE_SCORE_ALIASES = (
    "creative_confidence_score",
    "creative_confidence_1_100",
    "creative_abilities_confidence_score",
    "confidence_creative_abilities_score",
)
PARTICIPANT_START_TIME_CANDIDATES = (
    "speech_start_timestamp",
    "vad_start_timestamp",
    "mic_start_timestamp",
    "audio_start_timestamp",
    "segment_start_timestamp",
    "start_timestamp",
    "start_time",
)
PARTICIPANT_END_TIME_CANDIDATES = (
    "speech_end_timestamp",
    "vad_end_timestamp",
    "mic_end_timestamp",
    "audio_end_timestamp",
    "segment_end_timestamp",
    "end_timestamp",
    "end_time",
    "timestamp",
)
SESSION_EVALUATION_METRICS = (
    "elicitation_engagement_score",
    "creative_confidence_score",
)
BACKCHANNEL_RE = re.compile(
    r"\b(?:yeah|yep|yes|ja|mhm|mmhm|mm-hm|uh-huh|uh huh|right|okay|ok|exactly|true|sure|nice|cool|fair)\b",
    re.IGNORECASE,
)
LAUGHTER_RE = re.compile(r"\b(?:ha+ha+|hehe+|lol|laughs?|laughing)\b", re.IGNORECASE)
FILLER_RE = re.compile(r"\b(?:uh|um|erm|ehm|eh|like)\b", re.IGNORECASE)
LONG_PAUSE_SECONDS = 3.0
ADJACENCY_RESPONSE_SECONDS = 8.0
OVERLAP_MIN_SECONDS = 0.15
ELICITATION_WINDOW_ROBOT_TURN_CAP = 4
REQUIRED_CHART_FILENAMES = {
    "elicitation_engagement_by_phase_strategy.png",
    "connection_cue_rate_by_phase_strategy.png",
    "response_delay_by_phase_strategy.png",
    "speaking_time_by_phase_strategy.png",
    "vocal_activation_by_phase_strategy.png",
    "idea_fluency_by_phase_strategy.png",
    "elaboration_units_by_phase_strategy.png",
    "consecutive_topic_turns_by_phase_strategy.png",
}


@dataclass
class ChartSeries:
    labels: list[str]
    values: list[float]
    distributions: list[list[float]]
    ci95_half_widths: list[float]
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
    start_col = first_existing_column(df, PARTICIPANT_START_TIME_CANDIDATES)
    end_col = first_existing_column(df, PARTICIPANT_END_TIME_CANDIDATES)
    if "start_time" not in df.columns and start_col:
        df["start_time"] = df[start_col]
    if "end_time" not in df.columns and end_col:
        df["end_time"] = df[end_col]
    return df


def normalize_1_100_score(value: object) -> float:
    score = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(score) or float(score) < 1 or float(score) > 100:
        return np.nan
    return float(score)


def normalize_engagement_score(value: object) -> float:
    return normalize_1_100_score(value)


def count_words(text: object) -> int:
    return len(re.findall(r"\b\w+\b", str(text)))


def count_pattern(series: pd.Series, pattern: re.Pattern[str]) -> int:
    return int(
        sum(
            len(pattern.findall("" if pd.isna(raw_text) else str(raw_text)))
            for raw_text in series.tolist()
        )
    )


def count_short_backchannels(series: pd.Series) -> int:
    count = 0
    for raw_text in series.tolist():
        text = "" if pd.isna(raw_text) else str(raw_text)
        words = re.findall(r"\b\w+\b", text.lower())
        if 1 <= len(words) <= 5 and BACKCHANNEL_RE.search(text):
            count += 1
    return count


def nonverbal_event_mask(turns: pd.DataFrame) -> pd.Series:
    if "nonverbal_event" not in turns.columns:
        return pd.Series(False, index=turns.index)
    nonverbal = turns["nonverbal_event"].astype(str).str.strip().str.lower()
    return ~nonverbal.isin({"", "nan", "none"})


def compute_overlap_count(turns: pd.DataFrame) -> int:
    if len(turns) < 2:
        return 0
    spoken_turns = turns.copy()
    if "event_type" in spoken_turns.columns:
        spoken_turns = spoken_turns[
            spoken_turns["event_type"].astype(str).str.strip().str.lower().eq("participant")
        ].copy()
    spoken_turns = spoken_turns.dropna(subset=["start_s", "end_s"])
    if len(spoken_turns) < 2:
        return 0
    ordered = spoken_turns.sort_values(["start_s", "end_s"])
    overlaps = 0
    active: list[tuple[float, str]] = []
    for _, row in ordered.iterrows():
        speaker = str(row.get("speaker", "")).strip().lower()
        start = float(row["start_s"])
        end = float(row["end_s"])
        if not speaker or end <= start:
            continue
        active = [(active_end, active_speaker) for active_end, active_speaker in active if active_end > start]
        if any(
            active_speaker != speaker and min(end, active_end) - start >= OVERLAP_MIN_SECONDS
            for active_end, active_speaker in active
        ):
            overlaps += 1
        active.append((end, speaker))
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

    robot_turn_cap_by_index: dict[object, float] = {}
    robot_rows = df.copy()
    if "event_type" in robot_rows.columns:
        robot_rows = robot_rows[
            robot_rows["event_type"].astype(str).str.strip().str.lower().eq("robot")
        ].copy()
    robot_rows = robot_rows.sort_values(["session_id", "prompt_start_s", "prompt_end_s"])
    for robot_session_id, robot_group in robot_rows.groupby("session_id", sort=False):
        robot_group = robot_group.reset_index()
        for robot_position, robot_row in robot_group.iterrows():
            if not bool(robot_row.get("_is_elicitation", False)):
                continue
            cap_position = robot_position + ELICITATION_WINDOW_ROBOT_TURN_CAP
            if cap_position < len(robot_group):
                cap_start = float(robot_group.loc[cap_position, "prompt_start_s"])
                if cap_start > float(robot_row["prompt_end_s"]):
                    robot_turn_cap_by_index[robot_row["index"]] = cap_start

    transcript_end_by_session: dict[str, float] = {}
    phase_transition_times_by_session: dict[str, list[float]] = {}
    if transcript is not None and not transcript.empty:
        transcript_for_end = transcript.copy()
        if "event_type" in transcript_for_end.columns:
            event_type = transcript_for_end["event_type"].astype(str).str.strip().str.lower()
            transition_rows = transcript_for_end[event_type.isin({"phase_transition", "phase_change"})].copy()
            if not transition_rows.empty:
                for transition_session, transition_group in transition_rows.groupby("session_id", sort=False):
                    phase_transition_times_by_session[str(transition_session)] = sorted(
                        float(value) for value in transition_group["start_s"].dropna().tolist()
                    )
            transcript_for_end = transcript_for_end[
                ~event_type.isin({"session_start_evaluation", "session_end_evaluation", "phase_transition", "phase_change"})
            ].copy()
        transcript_end_by_session = transcript_for_end.groupby("session_id")["end_s"].max().to_dict()

    score_col = first_existing_column(df, ENGAGEMENT_SCORE_ALIASES)
    direct_scores: dict[tuple[str, str], list[float]] = {}
    previous_scores: dict[tuple[str, str], list[float]] = {}
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
                previous_scores.setdefault((session_key, previous_prompt), []).append(float(score))
                continue
            prompt_key = str(score_row.get("prompt_id", "")).strip()
            if prompt_key.lower() in {"nan", "none"}:
                prompt_key = ""
            if prompt_key:
                direct_scores.setdefault((session_key, prompt_key), []).append(float(score))

    direct_score_means = {
        key: float(np.mean(values))
        for key, values in direct_scores.items()
        if values
    }
    previous_score_means = {
        key: float(np.mean(values))
        for key, values in previous_scores.items()
        if values
    }

    windows: list[dict[str, object]] = []
    for session_id, session_rows in elic.sort_values(["session_id", "prompt_start_s"]).groupby("session_id", sort=False):
        rows = session_rows.reset_index()
        for i, row in rows.iterrows():
            next_start = np.nan
            if i + 1 < len(rows):
                next_start = float(rows.loc[i + 1, "prompt_start_s"])
            fallback_end = transcript_end_by_session.get(str(session_id), np.nan)
            robot_turn_cap_end = robot_turn_cap_by_index.get(row["index"], np.nan)
            transition_end = np.nan
            for transition_time in phase_transition_times_by_session.get(str(session_id), []):
                if transition_time > float(row["prompt_end_s"]) and (pd.isna(next_start) or transition_time < next_start):
                    transition_end = transition_time
                    break
            candidates = [
                float(row["prompt_end_s"]),
                robot_turn_cap_end,
                transition_end,
                next_start,
                float(explicit_end.loc[row["index"]]) if pd.notna(explicit_end.loc[row["index"]]) else np.nan,
                fallback_end,
            ]
            usable = [c for c in candidates[1:] if pd.notna(c) and c > float(row["prompt_end_s"])]
            window_end = min(usable) if usable else float(row["prompt_end_s"])
            cap_applied = pd.notna(robot_turn_cap_end) and abs(float(window_end) - float(robot_turn_cap_end)) < 1e-6
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
                    "robot_turn_cap_end_s": robot_turn_cap_end,
                    "robot_turn_cap_applied": cap_applied,
                    "elicitation_engagement_score": direct_score_means.get(
                        (str(session_id), str(row["prompt_id"]).strip()),
                        np.nan,
                    ),
                }
            )
    window_df = pd.DataFrame(windows)
    if previous_score_means and not window_df.empty:
        for (session_key, prompt_key), score in previous_score_means.items():
            mask = (
                window_df["session_id"].astype(str).eq(session_key)
                & window_df["prompt_id"].astype(str).str.strip().eq(prompt_key)
            )
            needs_score = mask & window_df["elicitation_engagement_score"].isna()
            window_df.loc[needs_score, "elicitation_engagement_score"] = score
    return window_df


def _clean_optional_text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def infer_evaluation_moment(row: pd.Series) -> str:
    moment = _clean_optional_text(row.get("evaluation_moment", "")).lower()
    if moment:
        return moment

    event_type = _clean_optional_text(row.get("event_type", "")).lower()
    if "start" in event_type or "baseline" in event_type:
        return "start"
    if "end" in event_type or "final" in event_type:
        return "end"
    if _clean_optional_text(row.get("previous_elicitation_prompt_id", "")):
        return "after_previous_elicitation"
    if _clean_optional_text(row.get("prompt_id", "")):
        return "direct_window"
    return "unspecified"


def extract_session_evaluation_scores(events: pd.DataFrame) -> pd.DataFrame:
    engagement_col = first_existing_column(events, ENGAGEMENT_SCORE_ALIASES)
    confidence_col = first_existing_column(events, CREATIVE_CONFIDENCE_SCORE_ALIASES)
    if not engagement_col and not confidence_col:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for _, row in events.iterrows():
        engagement = normalize_1_100_score(row.get(engagement_col)) if engagement_col else np.nan
        confidence = normalize_1_100_score(row.get(confidence_col)) if confidence_col else np.nan
        if pd.isna(engagement) and pd.isna(confidence):
            continue

        rows.append(
            {
                "session_id": row.get("session_id", ""),
                "group_id": row.get("group_id", ""),
                "conversation_id": row.get("conversation_id", ""),
                "event_type": row.get("event_type", ""),
                "speaker": row.get("speaker", ""),
                "evaluation_participant": row.get("evaluation_participant", row.get("speaker", "")),
                "evaluation_moment": infer_evaluation_moment(row),
                "timestamp": row.get("timestamp", ""),
                "phase": row.get("phase", ""),
                "strategy": row.get("strategy", ""),
                "prompt_id": row.get("prompt_id", ""),
                "previous_elicitation_prompt_id": row.get("previous_elicitation_prompt_id", ""),
                "elicitation_engagement_score": engagement,
                "creative_confidence_score": confidence,
            }
        )

    return pd.DataFrame(rows)


def compute_creative_confidence_change(evaluations: pd.DataFrame) -> pd.DataFrame:
    if evaluations.empty or "creative_confidence_score" not in evaluations.columns:
        return pd.DataFrame()

    confidence = evaluations.copy()
    confidence["creative_confidence_score"] = pd.to_numeric(confidence["creative_confidence_score"], errors="coerce")
    confidence = confidence.dropna(subset=["creative_confidence_score"])
    if confidence.empty or "evaluation_moment" not in confidence.columns:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    group_cols = ["session_id"]
    if "evaluation_participant" in confidence.columns:
        participant_values = confidence["evaluation_participant"].astype(str).str.strip()
        if participant_values.ne("").any():
            confidence["_evaluation_person"] = participant_values.where(participant_values.ne(""), confidence.get("speaker", ""))
            group_cols.append("_evaluation_person")
    elif "speaker" in confidence.columns:
        speaker_values = confidence["speaker"].astype(str).str.strip()
        if speaker_values.ne("").any():
            confidence["_evaluation_person"] = speaker_values
            group_cols.append("_evaluation_person")

    for group_key, session_rows in confidence.groupby(group_cols, dropna=False, sort=False):
        session_id = group_key[0] if isinstance(group_key, tuple) else group_key
        participant = group_key[1] if isinstance(group_key, tuple) and len(group_key) > 1 else ""
        start_rows = session_rows[session_rows["evaluation_moment"].astype(str).str.lower().eq("start")]
        end_rows = session_rows[session_rows["evaluation_moment"].astype(str).str.lower().eq("end")]
        if start_rows.empty or end_rows.empty:
            continue
        start_score = float(start_rows["creative_confidence_score"].mean())
        end_score = float(end_rows["creative_confidence_score"].mean())
        rows.append(
            {
                "session_id": session_id,
                "group_id": session_rows.iloc[0].get("group_id", ""),
                "conversation_id": session_rows.iloc[0].get("conversation_id", ""),
                "evaluation_participant": participant,
                "creative_confidence_start_score": start_score,
                "creative_confidence_end_score": end_score,
                "creative_confidence_change_score": end_score - start_score,
            }
        )
    return pd.DataFrame(rows)


def prepare_transcript(transcript: pd.DataFrame) -> pd.DataFrame:
    transcript = normalize_transcript_columns(transcript)
    require_columns(transcript, ["session_id", "speaker"], "transcript")
    df = transcript.copy()

    start_col = first_existing_column(df, PARTICIPANT_START_TIME_CANDIDATES)
    end_col = first_existing_column(df, PARTICIPANT_END_TIME_CANDIDATES)
    if end_col:
        df["end_s"] = parse_time_column(df[end_col], end_col)
    elif "duration_seconds" not in df.columns:
        raise ValueError(
            "transcript must include a participant end timestamp or duration_seconds. "
            "For Deepgram live logs, end_timestamp is the segment completion time."
        )

    # Response latency is defined from the end of Pepper's prompt to the first
    # participant microphone/VAD speech onset. Deepgram's displayed timestamp is
    # often the segment completion time, so prefer explicit start metadata.
    if start_col:
        df["start_s"] = parse_time_column(df[start_col], start_col)
    elif "duration_seconds" in df.columns and end_col:
        duration = pd.to_numeric(df["duration_seconds"], errors="coerce")
        if duration.isna().any():
            raise ValueError("duration_seconds contains non-numeric values.")
        df["start_s"] = df["end_s"] - duration
    else:
        raise ValueError(
            "transcript must include a microphone/VAD start timestamp for response latency "
            "or a duration_seconds column that can reconstruct it from the end timestamp."
        )

    if "end_time" in df.columns:
        df["end_s"] = parse_time_column(df["end_time"], "end_time")
    elif "duration_seconds" in df.columns:
        duration = pd.to_numeric(df["duration_seconds"], errors="coerce")
        if duration.isna().any():
            raise ValueError("duration_seconds contains non-numeric values.")
        df["end_s"] = df["start_s"] + duration

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
    if "event_type" in participant_turns.columns:
        event_type = participant_turns["event_type"].astype(str).str.strip().str.lower()
        participant_turns = participant_turns[event_type.isin({"participant", "nonverbal", ""})].copy()

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
            nonverbal_event_count = 0
            sigh_count = 0
            cough_count = 0
            impact_noise_count = 0
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
            coded_nonverbal_mask = nonverbal_event_mask(in_window)
            text_only_turns = in_window[~coded_nonverbal_mask]
            text_backchannel_count = count_short_backchannels(text_only_turns["text"])
            text_laughter_count = count_pattern(text_only_turns["text"], LAUGHTER_RE)
            text_filler_count = count_pattern(text_only_turns["text"], FILLER_RE)
            if "nonverbal_event" in in_window.columns:
                nonverbal = in_window["nonverbal_event"].astype(str).str.strip().str.lower()
                nonverbal_event_count = int(coded_nonverbal_mask.sum())
                laughter_event_count = int(nonverbal.isin({"laughter", "chuckle"}).sum())
                backchannel_event_count = int(nonverbal.eq("backchannel").sum())
                sigh_count = int(nonverbal.eq("sigh").sum())
                cough_count = int(nonverbal.eq("cough").sum())
                impact_noise_count = int(nonverbal.eq("impact_noise").sum())
                filler_event_count = int(nonverbal.eq("filler").sum())
            else:
                nonverbal_event_count = 0
                laughter_event_count = 0
                backchannel_event_count = 0
                sigh_count = 0
                cough_count = 0
                impact_noise_count = 0
                filler_event_count = 0
            backchannel_count = text_backchannel_count + backchannel_event_count
            laughter_count = text_laughter_count + laughter_event_count
            filler_count = text_filler_count + filler_event_count
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
                "nonverbal_event_count": nonverbal_event_count,
                "sigh_count": sigh_count,
                "cough_count": cough_count,
                "impact_noise_count": impact_noise_count,
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
    metrics["vocal_activation_score"] = (50.0 + 50.0 * activation_raw).clip(lower=0.0, upper=100.0).round(2)

    participant_speaking_minutes = (
        pd.to_numeric(metrics["participant_speaking_time_seconds"], errors="coerce") / 60.0
    ).replace(0, np.nan)
    connection_cue_count = (
        pd.to_numeric(metrics["backchannel_count"], errors="coerce").fillna(0.0)
        + pd.to_numeric(metrics["laughter_count"], errors="coerce").fillna(0.0)
        + pd.to_numeric(metrics["cooperative_overlap_count"], errors="coerce").fillna(0.0)
    )
    metrics["connection_cue_rate"] = (
        connection_cue_count / participant_speaking_minutes
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0).round(3)
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


def format_chart_label(label: object) -> str:
    return " ".join(str(label).replace("_", " ").replace("/", " / ").split())


def short_strategy_label(strategy: object) -> str:
    normalized = str(strategy).strip().lower()
    return {
        "elaboration_evidence": "elaborative",
        "perspective_shift": "perspective",
        "context_only": "context",
    }.get(normalized, format_chart_label(strategy))


def format_phase_strategy_label(phase: object, strategy: object) -> str:
    return f"{short_strategy_label(strategy)}\n{format_chart_label(phase)}"


def format_evaluation_moment_label(moment: object) -> str:
    normalized = str(moment).strip().lower()
    return {
        "after_previous_elicitation": "after\nelicitation",
        "direct_window": "direct\nwindow",
    }.get(normalized, format_chart_label(moment))


def draw_centered_x_label(
    draw: ImageDraw.ImageDraw,
    label: str,
    center_x: float,
    top_y: float,
    bold_font: ImageFont.ImageFont,
    regular_font: ImageFont.ImageFont,
) -> None:
    lines = str(label).splitlines()
    if len(lines) >= 2:
        strategy, phase = lines[0], lines[1]
        sw, sh = _text_size(draw, strategy, bold_font)
        pw, ph = _text_size(draw, phase, regular_font)
        draw.text((center_x - sw / 2, top_y), strategy, fill="#111827", font=bold_font)
        draw.text((center_x - pw / 2, top_y + sh + 6), phase, fill="#111827", font=regular_font)
        return

    text = lines[0] if lines else ""
    tw, _ = _text_size(draw, text, bold_font)
    draw.text((center_x - tw / 2, top_y), text, fill="#111827", font=bold_font)


def draw_bar_chart(series: ChartSeries) -> None:
    labels = series.labels
    values = [0.0 if pd.isna(v) else float(v) for v in series.values]
    if not labels:
        return

    width, height = 1600, 960
    margin_left, margin_right, margin_top, margin_bottom = 152, 48, 164, 176
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(56, bold=True)
    subtitle_font = _font(25)
    label_font = _font(28)
    label_bold_font = _font(38, bold=True)
    value_font = _font(48, bold=True)
    tick_font = _font(31)
    axis_font = _font(39, bold=True)

    palette = ["#3465a4", "#4e9a06", "#c17d11", "#75507b", "#cc0000", "#2e3436"]

    title_font_size = 56
    title_w, _ = _text_size(draw, series.title, title_font)
    while title_w > width - 70 and title_font_size > 38:
        title_font_size -= 2
        title_font = _font(title_font_size, bold=True)
        title_w, _ = _text_size(draw, series.title, title_font)
    draw.text(((width - title_w) / 2, 16), series.title, fill="#1f2933", font=title_font)
    subtitle = "Whiskers = 95% CI of mean"
    subtitle_w, subtitle_h = _text_size(draw, subtitle, subtitle_font)
    draw.text(((width - subtitle_w) / 2, 88), subtitle, fill="#64748b", font=subtitle_font)

    x0, y0 = margin_left, margin_top
    x1, y1 = width - margin_right, height - margin_bottom

    upper_ci_values = [
        float(value) + float(ci)
        for value, ci in zip(values, series.ci95_half_widths)
        if not pd.isna(value) and not pd.isna(ci)
    ]
    max_candidates = values + upper_ci_values
    max_value = max(max_candidates) if max_candidates else 1.0
    if max_value <= 0:
        max_value = 1.0
    y_max = max_value * 1.32

    n = len(labels)
    slot_w = plot_w / max(n, 1)
    bar_w = slot_w * 0.975

    draw.line([(x0, y1), (x1, y1)], fill="#2f3a45", width=3)
    draw.line([(x0, y0), (x0, y1)], fill="#2f3a45", width=3)

    tick_count = 5
    for i in range(tick_count + 1):
        frac = i / tick_count
        y = y1 - frac * plot_h
        val = y_max * frac
        draw.line([(x0 - 8, y), (x0, y)], fill="#2f3a45", width=2)
        draw.line([(x0, y), (x1, y)], fill="#e6e8eb", width=1)
        tick = f"{val:.1f}" if y_max < 20 else f"{val:.0f}"
        tw, th = _text_size(draw, tick, tick_font)
        draw.text((x0 - tw - 20, y - th / 2), tick, fill="#374151", font=tick_font)

    for i, (label, value) in enumerate(zip(labels, values)):
        center_x = x0 + slot_w * (i + 0.5)
        bar_x0 = center_x - bar_w / 2
        bar_x1 = center_x + bar_w / 2
        bar_h = 0 if y_max == 0 else (value / y_max) * plot_h
        bar_y0 = y1 - bar_h
        color = palette[i % len(palette)]
        draw.rounded_rectangle([bar_x0, bar_y0, bar_x1, y1], radius=5, fill=color)

        ci95_half_width = (
            0.0
            if i >= len(series.ci95_half_widths) or pd.isna(series.ci95_half_widths[i])
            else float(series.ci95_half_widths[i])
        )
        label_anchor_y = bar_y0
        if ci95_half_width > 0:
            upper_value = value + ci95_half_width
            lower_value = max(0.0, value - ci95_half_width)
            upper_y = y1 - (upper_value / y_max) * plot_h
            lower_y = y1 - (lower_value / y_max) * plot_h
            cap_half_width = bar_w * 0.22
            draw.line([(center_x, upper_y), (center_x, lower_y)], fill="#111827", width=6)
            draw.line(
                [(center_x - cap_half_width, upper_y), (center_x + cap_half_width, upper_y)],
                fill="#111827",
                width=6,
            )
            draw.line(
                [(center_x - cap_half_width, lower_y), (center_x + cap_half_width, lower_y)],
                fill="#111827",
                width=6,
            )
            label_anchor_y = upper_y

        value_text = f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"
        vw, vh = _text_size(draw, value_text, value_font)
        draw.text(
            (center_x - vw / 2, max(y0, label_anchor_y - vh - 28)),
            value_text,
            fill="#111827",
            font=value_font,
        )

        draw_centered_x_label(draw, label, center_x, y1 + 18, label_bold_font, label_font)

    yw, yh = _text_size(draw, series.y_label, axis_font)
    ylabel_img = Image.new("RGBA", (yw + 20, yh + 20), (255, 255, 255, 0))
    ydraw = ImageDraw.Draw(ylabel_img)
    ydraw.text((10, 10), series.y_label, fill="#1f2933", font=axis_font)
    rotated = ylabel_img.rotate(90, expand=True)
    image.paste(rotated, (18, int(y0 + plot_h / 2 - rotated.height / 2)), rotated)

    series.output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(series.output_path)


def chart_group_means(
    df: pd.DataFrame,
    metric: str,
    title: str,
    y_label: str,
    output_path: Path,
    group_cols: Sequence[str] | None = None,
) -> None:
    if metric not in df.columns:
        return
    work = df.copy()
    work[metric] = pd.to_numeric(work[metric], errors="coerce")
    if group_cols is None:
        group_cols = [c for c in ["phase", "strategy"] if c in work.columns]
    else:
        group_cols = [c for c in group_cols if c in work.columns]
    if not group_cols:
        group_cols = ["session_id"] if "session_id" in work.columns else []
    if not group_cols:
        return
    grouped = (
        work.groupby(group_cols, dropna=False)[metric]
        .agg(
            mean="mean",
            count="count",
            std="std",
            distribution=lambda values: [float(value) for value in values.dropna().tolist()],
        )
        .reset_index()
    )
    grouped = grouped.dropna(subset=["mean"])
    if grouped.empty:
        return
    grouped["sem"] = np.where(
        grouped["count"] > 1,
        grouped["std"] / np.sqrt(grouped["count"]),
        0.0,
    )
    grouped["ci95_half_width"] = grouped["sem"].fillna(0.0) * 1.96
    sort_cols = []
    if "strategy" in grouped.columns:
        grouped["_strategy_sort"] = (
            grouped["strategy"].astype(str).str.strip().str.lower().map(STRATEGY_SORT_ORDER).fillna(99)
        )
    if "phase" in grouped.columns:
        grouped["_phase_sort"] = (
            grouped["phase"].astype(str).str.strip().str.lower().map(PHASE_SORT_ORDER).fillna(99)
        )
    if "strategy" in grouped.columns and "phase" in grouped.columns:
        sort_cols.extend(["_phase_sort", "_strategy_sort"])
    else:
        if "phase" in grouped.columns:
            sort_cols.append("_phase_sort")
        if "strategy" in grouped.columns:
            sort_cols.append("_strategy_sort")
    if group_cols == ["evaluation_moment"] and "evaluation_moment" in grouped.columns:
        grouped["_moment_sort"] = (
            grouped["evaluation_moment"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map(EVALUATION_MOMENT_SORT_ORDER)
            .fillna(99)
        )
        sort_cols.append("_moment_sort")
    if sort_cols:
        grouped = grouped.sort_values(sort_cols, kind="stable").drop(columns=sort_cols)
    if "phase" in group_cols and "strategy" in group_cols:
        labels = [
            format_phase_strategy_label(row["phase"], row["strategy"])
            for _, row in grouped.iterrows()
        ]
    elif group_cols == ["evaluation_moment"]:
        labels = grouped["evaluation_moment"].map(format_evaluation_moment_label).tolist()
    else:
        labels = grouped[group_cols].astype(str).agg(" / ".join, axis=1).map(format_chart_label).tolist()
    values = grouped["mean"].astype(float).tolist()
    distributions = grouped["distribution"].tolist()
    ci95_half_widths = grouped["ci95_half_width"].astype(float).tolist()
    draw_bar_chart(
        ChartSeries(
            labels,
            values,
            distributions,
            ci95_half_widths,
            title,
            y_label,
            output_path,
        )
    )


AGGREGATE_PHASE_COLORS = {
    "divergence": "#0072B2",
    "convergence": "#D55E00",
}


def _aggregate_strategy_label(strategy: object) -> str:
    normalized = str(strategy).strip().lower()
    if normalized in {"elaboration_evidence", "elaborative"}:
        return "Elaborative"
    if normalized in {"perspective_shift", "perspective"}:
        return "Perspective"
    if normalized == "generative":
        return "Generative"
    return format_chart_label(strategy).title()


def _aggregate_metric_records(
    summary_df: pd.DataFrame | None,
    metric: str,
    label: str,
    unit: str,
) -> dict[str, object] | None:
    if summary_df is None or summary_df.empty:
        return None
    mean_col = f"{metric}_mean"
    count_col = f"{metric}_count"
    std_col = f"{metric}_std"
    required = ["phase", "strategy", mean_col, count_col, std_col]
    if any(col not in summary_df.columns for col in required):
        return None

    records: list[dict[str, object]] = []
    for _, row in summary_df.iterrows():
        mean = pd.to_numeric(pd.Series([row.get(mean_col)]), errors="coerce").iloc[0]
        if pd.isna(mean):
            continue
        count = pd.to_numeric(pd.Series([row.get(count_col)]), errors="coerce").iloc[0]
        std = pd.to_numeric(pd.Series([row.get(std_col)]), errors="coerce").iloc[0]
        ci95 = 0.0
        if pd.notna(count) and float(count) > 1 and pd.notna(std):
            ci95 = 1.96 * float(std) / math.sqrt(float(count))
        records.append(
            {
                "phase": str(row.get("phase", "")).strip().lower(),
                "strategy": str(row.get("strategy", "")).strip().lower(),
                "mean": float(mean),
                "ci95": float(ci95),
                "count": int(count) if pd.notna(count) else 0,
            }
        )
    if not records:
        return None
    return {"metric": metric, "label": label, "unit": unit, "records": records}


def _aggregate_count_label(metrics: Sequence[dict[str, object]]) -> str:
    counts = sorted(
        {
            int(record["count"])
            for metric in metrics
            for record in metric["records"]  # type: ignore[index]
            if int(record.get("count", 0)) > 0
        }
    )
    if not counts:
        return "n unavailable"
    if len(counts) == 1:
        return f"n = {counts[0]} groups/cell"
    return f"n = {counts[0]}-{counts[-1]} groups/cell"


def _nice_tick_step(span: float, target_ticks: int = 5) -> float:
    if span <= 0 or not np.isfinite(span):
        return 1.0
    raw_step = span / max(target_ticks - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if step >= raw_step:
            return step
    return 10 * magnitude


def _aggregate_axis_bounds(records: Sequence[dict[str, object]]) -> tuple[float, float, list[float]]:
    lows = [float(record["mean"]) - float(record["ci95"]) for record in records]
    highs = [float(record["mean"]) + float(record["ci95"]) for record in records]
    low = min(lows) if lows else 0.0
    high = max(highs) if highs else 1.0
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = 0.0, max(1.0, high)
    padding = max((high - low) * 0.15, high * 0.02, 0.08)
    axis_min = max(0.0, low - padding)
    axis_max = high + padding
    step = _nice_tick_step(axis_max - axis_min)
    tick_min = 0.0 if axis_min < step else math.floor(axis_min / step) * step
    tick_max = math.ceil(axis_max / step) * step
    ticks: list[float] = []
    tick = tick_min
    guard = 0
    while tick <= tick_max + step * 0.25 and guard < 20:
        ticks.append(round(tick, 8))
        tick += step
        guard += 1
    return tick_min, tick_max, ticks


def _format_aggregate_tick(value: float, ticks: Sequence[float]) -> str:
    diffs = [abs(ticks[i + 1] - ticks[i]) for i in range(len(ticks) - 1)]
    step = min(diffs) if diffs else 1.0
    if step < 0.1:
        return f"{value:.2f}"
    if step < 1:
        return f"{value:.1f}"
    if abs(value - round(value)) < 1e-8:
        return f"{value:.0f}"
    return f"{value:.1f}"


def _draw_right_aligned_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    right_x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    width, _ = _text_size(draw, text, font)
    draw.text((right_x - width, y), text, fill=fill, font=font)


def draw_horizontal_forest_aggregate_chart(
    title: str,
    metrics: Sequence[dict[str, object]],
    output_path: Path,
) -> None:
    if not metrics:
        return

    width = 1200
    header_h = 194
    panel_h = 265
    bottom_h = 34
    height = header_h + panel_h * len(metrics) + bottom_h
    margin_left = 255
    margin_right = 48
    plot_left = margin_left
    plot_right = width - margin_right
    plot_w = plot_right - plot_left

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(46, bold=True)
    stat_font = _font(24)
    key_font = _font(25, bold=True)
    metric_font = _font(33, bold=True)
    strategy_font = _font(30)
    tick_font = _font(27)
    unit_font = _font(27, bold=True)

    title_w, _ = _text_size(draw, title, title_font)
    draw.text(((width - title_w) / 2, 20), title, fill="#111827", font=title_font)
    _draw_right_aligned_text(
        draw,
        f"Means by phase and strategy; whiskers = 95% CI; {_aggregate_count_label(metrics)}",
        width - 42,
        78,
        stat_font,
        "#475569",
    )
    key_y = 112
    key_right = width - 42
    convergence_label = "Convergence"
    divergence_label = "Divergence"
    conv_w, _ = _text_size(draw, convergence_label, key_font)
    div_w, _ = _text_size(draw, divergence_label, key_font)
    slash_w, _ = _text_size(draw, "/", stat_font)
    key_x = key_right - conv_w - slash_w - div_w - 78
    draw.ellipse([key_x, key_y + 7, key_x + 20, key_y + 27], fill=AGGREGATE_PHASE_COLORS["divergence"])
    draw.text((key_x + 28, key_y), divergence_label, fill=AGGREGATE_PHASE_COLORS["divergence"], font=key_font)
    slash_x = key_x + 28 + div_w + 18
    draw.text((slash_x, key_y + 1), "/", fill="#64748B", font=stat_font)
    conv_dot_x = slash_x + slash_w + 20
    draw.ellipse([conv_dot_x, key_y + 7, conv_dot_x + 20, key_y + 27], fill=AGGREGATE_PHASE_COLORS["convergence"])
    draw.text(
        (conv_dot_x + 28, key_y),
        convergence_label,
        fill=AGGREGATE_PHASE_COLORS["convergence"],
        font=key_font,
    )

    strategy_order = sorted(STRATEGY_SORT_ORDER.items(), key=lambda item: item[1])
    strategy_keys = [key for key, _ in strategy_order if key in {"generative", "elaboration_evidence", "perspective_shift"}]
    phase_offsets = {"divergence": -9, "convergence": 9}

    for metric_index, metric in enumerate(metrics):
        panel_top = header_h + metric_index * panel_h
        records = metric["records"]  # type: ignore[index]
        label = str(metric["label"])
        unit = str(metric["unit"])
        x_min, x_max, ticks = _aggregate_axis_bounds(records)  # type: ignore[arg-type]
        plot_top = panel_top + 67
        plot_bottom = panel_top + panel_h - 68
        plot_mid_h = plot_bottom - plot_top
        base_y = {
            strategy: plot_top + 20 + idx * (plot_mid_h - 40) / max(len(strategy_keys) - 1, 1)
            for idx, strategy in enumerate(strategy_keys)
        }

        draw.text((plot_left, panel_top + 12), label, fill="#111827", font=metric_font)
        for tick in ticks:
            x = plot_left + (float(tick) - x_min) / max(x_max - x_min, 1e-9) * plot_w
            draw.line([(x, plot_top - 7), (x, plot_bottom)], fill="#E2E8F0", width=2)
            tick_label = _format_aggregate_tick(float(tick), ticks)
            tick_w, tick_h = _text_size(draw, tick_label, tick_font)
            draw.text((x - tick_w / 2, plot_bottom + 8), tick_label, fill="#334155", font=tick_font)
        draw.line([(plot_left, plot_bottom), (plot_right, plot_bottom)], fill="#CBD5E1", width=4)
        draw.line([(plot_left, plot_top - 7), (plot_left, plot_bottom)], fill="#CBD5E1", width=3)
        for first_strategy, second_strategy in zip(strategy_keys, strategy_keys[1:]):
            separator_y = (base_y[first_strategy] + base_y[second_strategy]) / 2
            draw.line(
                [(plot_left, separator_y), (plot_right, separator_y)],
                fill="#F1F5F9",
                width=2,
            )

        for strategy in strategy_keys:
            y = base_y[strategy]
            strategy_label = _aggregate_strategy_label(strategy)
            text_w, text_h = _text_size(draw, strategy_label, strategy_font)
            draw.text(
                (plot_left - text_w - 28, y - text_h / 2),
                strategy_label,
                fill="#334155",
                font=strategy_font,
            )
            draw.line([(plot_left - 10, y), (plot_left, y)], fill="#64748B", width=2)

        for record in records:  # type: ignore[assignment]
            phase = str(record["phase"])
            strategy = str(record["strategy"])
            if strategy not in base_y or phase not in AGGREGATE_PHASE_COLORS:
                continue
            mean = float(record["mean"])
            ci95 = float(record["ci95"])
            y = base_y[strategy] + phase_offsets[phase]
            x = plot_left + (mean - x_min) / max(x_max - x_min, 1e-9) * plot_w
            low_x = plot_left + (max(x_min, mean - ci95) - x_min) / max(x_max - x_min, 1e-9) * plot_w
            high_x = plot_left + (min(x_max, mean + ci95) - x_min) / max(x_max - x_min, 1e-9) * plot_w
            color = AGGREGATE_PHASE_COLORS[phase]
            draw.line([(low_x, y), (high_x, y)], fill=color, width=5)
            draw.line([(low_x, y - 10), (low_x, y + 10)], fill=color, width=4)
            draw.line([(high_x, y - 10), (high_x, y + 10)], fill=color, width=4)
            draw.ellipse([x - 11, y - 11, x + 11, y + 11], fill=color)

        unit_w, unit_h = _text_size(draw, unit, unit_font)
        draw.text(
            (plot_left + plot_w / 2 - unit_w / 2, plot_bottom + 39),
            unit,
            fill="#475569",
            font=unit_font,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, dpi=(300, 300))


def draw_aggregate_measurement_charts(
    engagement_summary: pd.DataFrame | None,
    transcript_summary: pd.DataFrame | None,
    manual_summary: pd.DataFrame | None,
    chart_dir: Path,
) -> None:
    engagement_metrics = [
        item
        for item in [
            _aggregate_metric_records(
                engagement_summary,
                "elicitation_engagement_score",
                "Self-reported engagement",
                "1-100 score",
            ),
            _aggregate_metric_records(
                transcript_summary,
                "connection_cue_rate",
                "Connection cue rate",
                "cues/min speaking",
            ),
            _aggregate_metric_records(
                transcript_summary,
                "response_delay_seconds",
                "Response delay",
                "seconds",
            ),
            _aggregate_metric_records(
                transcript_summary,
                "participant_speaking_time_seconds",
                "Speaking time",
                "seconds",
            ),
            _aggregate_metric_records(
                transcript_summary,
                "vocal_activation_score",
                "Vocal activation",
                "0-100 index",
            ),
        ]
        if item is not None
    ]
    if engagement_metrics:
        draw_horizontal_forest_aggregate_chart(
            "Engagement Measurements",
            engagement_metrics,
            chart_dir / "engagement_measurements_aggregate.png",
        )

    substantive_metrics = [
        item
        for item in [
            _aggregate_metric_records(
                manual_summary,
                "idea_fluency",
                "Idea count",
                "distinct ideas",
            ),
            _aggregate_metric_records(
                manual_summary,
                "elaboration_units",
                "Elaboration units",
                "units",
            ),
            _aggregate_metric_records(
                manual_summary,
                "consecutive_topic_turns",
                "Same-subject turns",
                "turns",
            ),
        ]
        if item is not None
    ]
    if substantive_metrics:
        draw_horizontal_forest_aggregate_chart(
            "Substantive Contribution Measurements",
            substantive_metrics,
            chart_dir / "substantive_contribution_measurements_aggregate.png",
        )


def clean_chart_outputs(output_dir: Path, chart_dir: Path) -> None:
    if chart_dir.exists():
        for path in chart_dir.glob("*.png"):
            if path.name not in REQUIRED_CHART_FILENAMES:
                path.unlink()
    legacy_overview_path = output_dir / ("pepper_measurement_" + "dashboard.png")
    if legacy_overview_path.exists():
        legacy_overview_path.unlink()


def chart_output_count(chart_dir: Path) -> int:
    return sum(1 for name in REQUIRED_CHART_FILENAMES if (chart_dir / name).exists())


def write_templates(output_dir: str | Path) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    templates = {
        "transcript_template.csv": [
            ["session_id", "speaker", "start_time", "end_time", "text"],
            ["S01", "P1", "2026-05-05T10:00:05", "2026-05-05T10:00:12", "Maybe we could use peer mentors."],
        ],
        "intervention_log_template.csv": [
            [
                "session_id",
                "prompt_id",
                "phase",
                "strategy",
                "prompt_type",
                "prompt_start_time",
                "prompt_end_time",
                "window_end_time",
                "evaluation_participant",
                "elicitation_engagement_score",
                "creative_confidence_score",
                "evaluation_moment",
            ],
            ["S01", "", "divergence", "session_start", "evaluation", "2026-05-05T09:59:50", "2026-05-05T09:59:50", "", "Participant 1", "55", "", "start"],
            ["S01", "", "divergence", "session_start", "evaluation", "2026-05-05T09:59:52", "2026-05-05T09:59:52", "", "Participant 2", "58", "", "start"],
            ["S01", "E01", "divergence", "generative", "elicitation", "2026-05-05T10:00:00", "2026-05-05T10:00:04", "", "", "", "", ""],
            ["S01", "E01", "divergence", "generative", "evaluation", "2026-05-05T10:02:00", "2026-05-05T10:02:00", "", "Participant 1", "72", "", "after_previous_elicitation"],
            ["S01", "E01", "divergence", "generative", "evaluation", "2026-05-05T10:02:01", "2026-05-05T10:02:01", "", "Participant 2", "76", "", "after_previous_elicitation"],
            ["S01", "E02", "divergence", "elaboration_evidence", "evaluation", "2026-05-05T10:08:00", "2026-05-05T10:08:00", "", "Participant 1", "", "74", "end"],
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
    clean_chart_outputs(output_dir, chart_dir)

    transcript = None
    interventions = None
    windows = None
    combined_rows: list[pd.DataFrame] = []
    summary_rows: list[pd.DataFrame] = []
    engagement_summary: pd.DataFrame | None = None
    transcript_summary: pd.DataFrame | None = None
    manual_summary: pd.DataFrame | None = None

    if args.transcript:
        transcript = prepare_transcript(read_csv(args.transcript))

    if args.interventions:
        interventions = read_csv(args.interventions)

    if args.interventions:
        session_evaluations = extract_session_evaluation_scores(interventions)
        if not session_evaluations.empty:
            session_evaluations.to_csv(output_dir / "session_evaluation_scores.csv", index=False)
            session_evaluation_summary = summarize_numeric(
                session_evaluations,
                SESSION_EVALUATION_METRICS,
                ["evaluation_moment"],
            )
            session_evaluation_summary.to_csv(output_dir / "session_evaluation_summary.csv", index=False)
            summary_rows.append(session_evaluation_summary)
            confidence_change = compute_creative_confidence_change(session_evaluations)
            if not confidence_change.empty:
                confidence_change.to_csv(output_dir / "creative_confidence_change_by_session.csv", index=False)

        windows = build_windows(interventions, transcript)
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
                "Mean Self-Reported Engagement Score",
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
                "nonverbal_event_count",
                "sigh_count",
                "cough_count",
                "impact_noise_count",
                "cooperative_overlap_count",
                "adjacency_response_success",
                "connection_cues_per_minute",
                "vocal_activation_score",
                "connection_cue_rate",
            ],
            ["phase", "strategy"],
        )
        transcript_summary.to_csv(output_dir / "transcript_summary_by_phase_strategy.csv", index=False)
        summary_rows.append(transcript_summary)
        chart_group_means(
            transcript_metrics,
            "response_delay_seconds",
            "Mean Response Delay",
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
            "connection_cue_rate",
            "Mean Connection Cue Rate",
            "Cues per speaking minute",
            chart_dir / "connection_cue_rate_by_phase_strategy.png",
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
        chart_group_means(manual, "idea_fluency", "Mean Idea Count", "Distinct ideas", chart_dir / "idea_fluency_by_phase_strategy.png")
        chart_group_means(manual, "elaboration_units", "Mean Elaboration Units", "Elaborative units", chart_dir / "elaboration_units_by_phase_strategy.png")
        chart_group_means(
            manual,
            "consecutive_topic_turns",
            "Mean Consecutive Turns on Same Subject",
            "Turns",
            chart_dir / "consecutive_topic_turns_by_phase_strategy.png",
        )

    manifest = {
        "output_dir": str(output_dir.resolve()),
        "charts_dir": str(chart_dir.resolve()),
        "chart_png_count": chart_output_count(chart_dir),
        "expected_chart_pngs": sorted(REQUIRED_CHART_FILENAMES),
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
