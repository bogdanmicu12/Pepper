from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import matplotlib.pyplot as plt
import numpy as np


DEFAULT_ORIGINAL_TRANSCRIPT = Path("llm/logs/synthetic_tu_delft_campus_experience/transcript.csv")
DEFAULT_NO_PHASE_TRANSCRIPT = Path("llm/Participation inequality/proactive_supportive_no_phase_transcript.csv")
DEFAULT_OUTPUT_ROOT = Path("llm/Participation inequality/results")
PARTICIPANT_EVENT = "participant"
METRICS = ("word_count", "turn_count", "speech_time_seconds")


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", clean(value)).strip("_")
    return name or "transcript"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing table: {path}")
    if not rows:
        raise ValueError(f"No rows to write for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def parse_timestamp(value: str) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def speech_seconds(row: dict[str, str]) -> float:
    start = parse_timestamp(row.get("start_timestamp", ""))
    end = parse_timestamp(row.get("end_timestamp", ""))
    if not start or not end:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def adjusted_gini(values: Sequence[float]) -> tuple[float, float]:
    numeric = [max(0.0, float(value)) for value in values]
    n = len(numeric)
    total = sum(numeric)
    if n < 2 or total <= 0:
        return 0.0, 0.0
    pairwise_abs = sum(abs(a - b) for a in numeric for b in numeric)
    standard_gini = pairwise_abs / (2.0 * n * total)
    finite_sample_max = (n - 1.0) / n
    adjusted = standard_gini / finite_sample_max if finite_sample_max else 0.0
    return min(1.0, adjusted), standard_gini


def interpret(value: float) -> str:
    if value < 0.10:
        return "very equal"
    if value < 0.25:
        return "mild inequality"
    if value < 0.45:
        return "moderate inequality"
    return "high inequality"


def participant_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if clean(row.get("event_type")).lower() == PARTICIPANT_EVENT]


def infer_groups(rows: Sequence[dict[str, str]], phase_mode: str) -> list[str]:
    if phase_mode == "none":
        return ["overall"]
    phases = sorted({clean(row.get("phase")) for row in rows if clean(row.get("phase"))})
    if phase_mode == "phase" and not phases:
        raise ValueError("Phase mode was requested, but no non-empty phase values were found.")
    if phases:
        return phases + ["overall"]
    return ["overall"]


def compute_table(rows: Sequence[dict[str, str]], dataset_name: str, phase_mode: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    participants = participant_rows(rows)
    if not participants:
        raise ValueError("No participant rows found. Expected event_type=participant.")
    speakers = sorted({clean(row.get("speaker")) for row in participants if clean(row.get("speaker"))})
    if len(speakers) < 2:
        raise ValueError("Adjusted Gini needs at least two participant speakers.")

    groups = infer_groups(participants, phase_mode)
    result_rows: list[dict[str, object]] = []
    totals_rows: list[dict[str, object]] = []

    for group in groups:
        selected = participants if group == "overall" else [
            row for row in participants if clean(row.get("phase")) == group
        ]
        speaker_totals = {
            speaker: {
                "word_count": 0.0,
                "turn_count": 0.0,
                "speech_time_seconds": 0.0,
            }
            for speaker in speakers
        }
        for row in selected:
            speaker = clean(row.get("speaker"))
            speaker_totals[speaker]["word_count"] += word_count(clean(row.get("text")))
            speaker_totals[speaker]["turn_count"] += 1
            speaker_totals[speaker]["speech_time_seconds"] += speech_seconds(row)

        for speaker in speakers:
            totals_rows.append(
                {
                    "dataset": dataset_name,
                    "analysis_group": group,
                    "speaker": speaker,
                    "word_count": int(speaker_totals[speaker]["word_count"]),
                    "turn_count": int(speaker_totals[speaker]["turn_count"]),
                    "speech_time_seconds": round(speaker_totals[speaker]["speech_time_seconds"], 3),
                }
            )

        for metric in METRICS:
            values = [speaker_totals[speaker][metric] for speaker in speakers]
            adjusted, standard = adjusted_gini(values)
            result_rows.append(
                {
                    "dataset": dataset_name,
                    "analysis_group": group,
                    "metric": metric,
                    "adjusted_gini": round(adjusted, 4),
                    "standard_gini": round(standard, 4),
                    "participant_count": len(speakers),
                    "total_value": round(sum(values), 3),
                    "min_participant_value": round(min(values), 3),
                    "max_participant_value": round(max(values), 3),
                    "interpretation": interpret(adjusted),
                }
            )
    summary_rows: list[dict[str, object]] = []
    for group in groups:
        row = {"dataset": dataset_name, "analysis_group": group}
        for metric in METRICS:
            for r in result_rows:
                if r["analysis_group"] == group and r["metric"] == metric:
                    row[f"{metric}_gini"] = r["adjusted_gini"]
                    break
        summary_rows.append(row)

    return result_rows, totals_rows, summary_rows


def make_run_dir(output_root: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / base
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = output_root / f"{base}_{suffix:02d}"
    run_dir.mkdir(parents=True)
    return run_dir


def print_table(rows: Sequence[dict[str, object]], title: str) -> None:
    print("")
    print(title)
    print("-" * len(title))
    columns = ["analysis_group", "metric", "adjusted_gini", "standard_gini", "total_value", "interpretation"]
    widths = {column: len(column) for column in columns}
    for row in rows:
        for column in columns:
            widths[column] = max(widths[column], len(str(row[column])))
    header = "  ".join(column.ljust(widths[column]) for column in columns)
    print(header)
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(str(row[column]).ljust(widths[column]) for column in columns))


def save_summary_chart(summary: Sequence[dict[str, object]], dataset_name: str, chart_path: Path) -> None:
    if not summary:
        return

    groups = sorted({str(row["analysis_group"]) for row in summary})
    metrics = ["word_count_gini", "turn_count_gini", "speech_time_seconds_gini"]
    x_positions = np.arange(len(metrics))
    bar_width = 0.8 / len(groups) if groups else 0.8

    fig, ax = plt.subplots(figsize=(12, 8))
    colors = ["#2e86c1", "#d68910", "#229954", "#e74c3c", "#9b59b6"]
    for group_index, group in enumerate(groups):
        values = [
            next(
                (
                    float(row[metric])
                    for row in summary
                    if row["analysis_group"] == group
                ),
                0.0,
            )
            for metric in metrics
        ]
        offsets = x_positions + group_index * bar_width - bar_width * (len(groups) - 1) / 2
        bars = ax.bar(
            offsets,
            values,
            bar_width,
            color=colors[group_index % len(colors)],
            edgecolor="black",
            linewidth=0.5,
            label=group,
        )

        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.01,
                f"{value:.3f}",
                ha="center",
                va="bottom",
                fontsize=10,
                fontweight="bold",
                color="black",
            )

    ax.set_xlabel("Participation Metrics", fontsize=14, fontweight="bold")
    ax.set_ylabel("Adjusted Gini Coefficient", fontsize=14, fontweight="bold")
    ax.set_title(f"{dataset_name}: Participation Inequality Analysis", fontsize=16, fontweight="bold", pad=20)
    ax.set_xticks(x_positions)
    ax.set_xticklabels([metric.replace("_gini", "").replace("_", " ").title() for metric in metrics], fontsize=12)
    ax.set_ylim(0, 1.1)
    ax.grid(True, alpha=0.3, axis="y")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if len(groups) > 1:
        ax.legend(frameon=False)

    chart_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def analyze_one(transcript: Path, dataset_name: str, phase_mode: str, run_dir: Path) -> Path:
    table, totals, summary = compute_table(read_csv(transcript), dataset_name, phase_mode)
    dataset_safe = safe_name(dataset_name)
    table_path = run_dir / f"{dataset_safe}_gini.csv"
    totals_path = run_dir / f"{dataset_safe}_participant_totals.csv"
    summary_path = run_dir / f"{dataset_safe}_summary_gini.csv"
    write_csv(table_path, table)
    write_csv(totals_path, totals)
    write_csv(summary_path, summary)
    print_table(table, f"{dataset_name}: adjusted Gini results")
    print(f"Saved table: {table_path}")
    print(f"Saved participant totals: {totals_path}")
    print(f"Saved summary table: {summary_path}")
    print("")
    print(f"{dataset_name}: summary Gini table")
    print("-" * len(f"{dataset_name}: summary Gini table"))
    for row in summary:
        print(f"{row['analysis_group']}: word_count_gini={row['word_count_gini']}, turn_count_gini={row['turn_count_gini']}, speech_time_seconds_gini={row['speech_time_seconds_gini']}")

    chart_path = run_dir / f"{dataset_safe}_gini_chart.png"
    save_summary_chart(summary, dataset_name, chart_path)
    if summary:
        print(f"Saved chart: {chart_path}")

    return table_path


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compute adjusted Gini participation inequality for one or more transcripts.")
    parser.add_argument("--demo", action="store_true", help="Run the two bundled demo transcripts and write two separate result tables.")
    parser.add_argument("--transcript", help="Path to one transcript CSV.")
    parser.add_argument("--dataset-name", default="", help="Name used in the output table filename and dataset column.")
    parser.add_argument("--phase-mode", choices=["auto", "phase", "none"], default="auto", help="Use phases, force phases, or ignore phases.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root folder where timestamped run folders are created.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_dir = make_run_dir(Path(args.output_root))

    if args.demo:
        analyze_one(DEFAULT_ORIGINAL_TRANSCRIPT, "original_phased", "auto", run_dir)
        analyze_one(DEFAULT_NO_PHASE_TRANSCRIPT, "proactive_supportive_no_phase", "auto", run_dir)
        print(f"\nCreated separate demo result tables in: {run_dir}")
        return 0

    if not args.transcript:
        raise SystemExit("Pass --transcript PATH, or use --demo for the bundled examples.")

    transcript = Path(args.transcript)
    dataset_name = args.dataset_name or transcript.stem
    analyze_one(transcript, dataset_name, args.phase_mode, run_dir)
    print(f"\nCreated result table in: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
