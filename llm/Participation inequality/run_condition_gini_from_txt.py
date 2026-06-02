from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


DEFAULT_CONVERSATIONS_ROOT = Path("llm/logs/conversations")
DEFAULT_OUTPUT_ROOT = Path("llm/Participation inequality/results")
METRICS = ("word_count", "turn_count")
TURN_RE = re.compile(r"^\[(?P<speaker>[^\]]+)\]:\s*(?P<text>.*)$")


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", clean(value)).strip("_")
    return name or "condition"


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def is_participant(speaker: str) -> bool:
    normalized = clean(speaker).lower()
    return normalized.startswith("participant")


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


def read_txt_turns(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Conversation file not found: {path}")

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            match = TURN_RE.match(text)
            if not match:
                print(f"Warning: skipping unparsable line {path}:{line_number}: {text}", file=sys.stderr)
                continue
            rows.append(
                {
                    "speaker": clean(match.group("speaker")),
                    "text": clean(match.group("text")),
                }
            )
    return rows


def compute_conversation(
    condition: str,
    conversation_path: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    turns = [row for row in read_txt_turns(conversation_path) if is_participant(row["speaker"])]
    if not turns:
        raise ValueError(f"No participant turns found in {conversation_path}")

    speakers = sorted({row["speaker"] for row in turns if row["speaker"]})
    if len(speakers) < 2:
        raise ValueError(f"Adjusted Gini needs at least two participant speakers in {conversation_path}")

    speaker_totals = {
        speaker: {
            "word_count": 0.0,
            "turn_count": 0.0,
        }
        for speaker in speakers
    }
    for row in turns:
        speaker = row["speaker"]
        speaker_totals[speaker]["word_count"] += word_count(row["text"])
        speaker_totals[speaker]["turn_count"] += 1

    conversation_name = conversation_path.stem
    totals_rows: list[dict[str, object]] = []
    for speaker in speakers:
        totals_rows.append(
            {
                "condition": condition,
                "conversation": conversation_name,
                "speaker": speaker,
                "word_count": int(speaker_totals[speaker]["word_count"]),
                "turn_count": int(speaker_totals[speaker]["turn_count"]),
            }
        )

    gini_rows: list[dict[str, object]] = []
    for metric in METRICS:
        values = [speaker_totals[speaker][metric] for speaker in speakers]
        adjusted, standard = adjusted_gini(values)
        gini_rows.append(
            {
                "condition": condition,
                "conversation": conversation_name,
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
    return gini_rows, totals_rows


def summarize_condition_averages(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        key = (str(row["condition"]), str(row["metric"]))
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict[str, object]] = []
    for condition, metric in sorted(grouped):
        metric_rows = grouped[(condition, metric)]
        adjusted_values = [float(row["adjusted_gini"]) for row in metric_rows]
        standard_values = [float(row["standard_gini"]) for row in metric_rows]
        summary_rows.append(
            {
                "condition": condition,
                "metric": metric,
                "conversation_count": len(metric_rows),
                "mean_adjusted_gini": round(sum(adjusted_values) / len(adjusted_values), 4),
                "mean_standard_gini": round(sum(standard_values) / len(standard_values), 4),
                "min_adjusted_gini": round(min(adjusted_values), 4),
                "max_adjusted_gini": round(max(adjusted_values), 4),
            }
        )
    return summary_rows


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


def save_condition_average_chart(rows: Sequence[dict[str, object]], path: Path) -> None:
    conditions = sorted({str(row["condition"]) for row in rows})
    metrics = list(METRICS)
    colors = {
        "word_count": "#2e86c1",
        "turn_count": "#d68910",
    }

    fig_width = max(8.0, 2.0 + len(conditions) * 1.4)
    fig, ax = plt.subplots(figsize=(fig_width, 6.0))
    x_positions = list(range(len(conditions)))
    bar_width = 0.34

    for metric_index, metric in enumerate(metrics):
        offsets = [
            x + (metric_index - (len(metrics) - 1) / 2.0) * bar_width
            for x in x_positions
        ]
        values = [
            next(
                (
                    float(row["mean_adjusted_gini"])
                    for row in rows
                    if row["condition"] == condition and row["metric"] == metric
                ),
                0.0,
            )
            for condition in conditions
        ]
        bars = ax.bar(
            offsets,
            values,
            bar_width,
            label=metric.replace("_", " ").title(),
            color=colors.get(metric, "#6c757d"),
            edgecolor="black",
            linewidth=0.6,
        )
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 0.01,
                f"{value:.3f}",
                ha="center",
                va="bottom",
                fontsize=10,
            )

    ax.set_title("Average Participation Inequality By Condition", fontsize=15, fontweight="bold", pad=14)
    ax.set_xlabel("Condition", fontsize=12)
    ax.set_ylabel("Mean Adjusted Gini", fontsize=12)
    ax.set_xticks(x_positions)
    ax.set_xticklabels(conditions, rotation=20, ha="right")
    ax.set_ylim(0.0, 1.0)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(frameon=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


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


def condition_dirs(root: Path, condition: str) -> list[Path]:
    if condition:
        path = root / condition
        if not path.is_dir():
            raise FileNotFoundError(f"Condition folder not found: {path}")
        return [path]
    return sorted(path for path in root.iterdir() if path.is_dir())


def analyze_conversation_file(conversation_file: Path, condition: str, output_root: Path) -> Path:
    if not conversation_file.is_file():
        raise FileNotFoundError(f"Conversation file not found: {conversation_file}")

    run_dir = make_run_dir(output_root)
    condition_name = condition or conversation_file.parent.name
    gini_rows, totals_rows = compute_conversation(condition_name, conversation_file)
    summary_rows = summarize_condition_averages(gini_rows)

    write_csv(run_dir / "conversation_gini.csv", gini_rows)
    write_csv(run_dir / "participant_totals_from_txt.csv", totals_rows)
    write_csv(run_dir / "condition_average_gini.csv", summary_rows)
    chart_path = run_dir / "condition_average_gini.png"
    save_condition_average_chart(summary_rows, chart_path)

    print(f"Saved per-conversation Gini: {run_dir / 'conversation_gini.csv'}")
    print(f"Saved participant totals: {run_dir / 'participant_totals_from_txt.csv'}")
    print(f"Saved condition averages: {run_dir / 'condition_average_gini.csv'}")
    print(f"Saved condition average chart: {chart_path}")
    print("")
    print("Conversation adjusted Gini")
    print("--------------------------")
    for row in gini_rows:
        print(
            f"{row['condition']} / {row['conversation']} / {row['metric']}: "
            f"adjusted_gini={row['adjusted_gini']}"
        )
    return run_dir


def analyze_conditions(root: Path, condition: str, output_root: Path) -> Path:
    if not root.is_dir():
        raise FileNotFoundError(f"Conversations root not found: {root}")

    run_dir = make_run_dir(output_root)
    all_gini_rows: list[dict[str, object]] = []
    all_totals_rows: list[dict[str, object]] = []

    for condition_dir in condition_dirs(root, condition):
        txt_files = sorted(condition_dir.glob("*.txt"))
        if not txt_files:
            print(f"Warning: no .txt files found in {condition_dir}", file=sys.stderr)
            continue
        for txt_file in txt_files:
            gini_rows, totals_rows = compute_conversation(condition_dir.name, txt_file)
            all_gini_rows.extend(gini_rows)
            all_totals_rows.extend(totals_rows)

    if not all_gini_rows:
        raise ValueError("No conversation Gini rows were produced.")

    summary_rows = summarize_condition_averages(all_gini_rows)
    write_csv(run_dir / "conversation_gini.csv", all_gini_rows)
    write_csv(run_dir / "participant_totals_from_txt.csv", all_totals_rows)
    write_csv(run_dir / "condition_average_gini.csv", summary_rows)
    chart_path = run_dir / "condition_average_gini.png"
    save_condition_average_chart(summary_rows, chart_path)

    print(f"Saved per-conversation Gini: {run_dir / 'conversation_gini.csv'}")
    print(f"Saved participant totals: {run_dir / 'participant_totals_from_txt.csv'}")
    print(f"Saved condition averages: {run_dir / 'condition_average_gini.csv'}")
    print(f"Saved condition average chart: {chart_path}")
    print("")
    print("Condition average adjusted Gini")
    print("--------------------------------")
    for row in summary_rows:
        print(
            f"{row['condition']} / {row['metric']}: "
            f"mean_adjusted_gini={row['mean_adjusted_gini']} "
            f"(n={row['conversation_count']})"
        )
    return run_dir


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute adjusted Gini participation inequality from conversation .txt files grouped by condition."
    )
    parser.add_argument("--conversations-root", default=str(DEFAULT_CONVERSATIONS_ROOT), help="Folder containing condition subfolders.")
    parser.add_argument("--condition", default="", help="Analyze one condition folder, for example Proactive_Assertive. Defaults to all condition folders.")
    parser.add_argument("--conversation-file", default="", help="Analyze one conversation .txt file directly.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Root folder where timestamped run folders are created.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.conversation_file:
        run_dir = analyze_conversation_file(Path(args.conversation_file), args.condition, Path(args.output_root))
    else:
        run_dir = analyze_conditions(Path(args.conversations_root), args.condition, Path(args.output_root))
    print(f"\nCreated TXT conversation Gini results in: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
