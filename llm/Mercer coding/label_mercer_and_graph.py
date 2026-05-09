from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError as error:
    raise SystemExit(
        "Pillow is required to draw PNG charts. Install it or run with the project's Python environment."
    ) from error


DEFAULT_TRANSCRIPT = Path("llm/logs/synthetic_tu_delft_campus_experience/transcript.csv")
DEFAULT_OUTPUT_DIR = Path("llm/Mercer coding/outputs")
PARTICIPANT_EVENT_TYPE = "participant"
MERCER_LABELS = ("disputational", "cumulative", "exploratory")

AGREEMENT_RE = re.compile(
    r"\b(?:i agree|yes|yeah|yep|exactly|that is true|that's true|that is fair|"
    r"that's fair|good point|i like|sounds better|makes sense|fair|true)\b",
    re.IGNORECASE,
)
DISAGREEMENT_RE = re.compile(
    r"\b(?:i disagree|i partly disagree|i do not think|i don't think|but|however|"
    r"although|not sure|i worry|risk|objection|not the same|terrible|dismiss|"
    r"too many|only|instead|rather|without|not just|not only)\b",
    re.IGNORECASE,
)
REASONING_RE = re.compile(
    r"\b(?:because|so|if|then|therefore|that means|which means|as a result|"
    r"for example|like|since|the goal|the issue|the problem|the point|"
    r"compared with|different from|depends|decides|reveals|affects)\b",
    re.IGNORECASE,
)
COCONSTRUCTION_RE = re.compile(
    r"\b(?:we could|we can|we need|we should|maybe|should|could|would|need|needs|"
    r"it becomes|it should|that changes|connects back|builds on|proposal|"
    r"solution|route|include|test|overcome|avoid|handle|maintained|available|"
    r"first version|final framing|options|versions)\b",
    re.IGNORECASE,
)
QUESTION_RE = re.compile(r"\?")
SHORT_CONFIRMATION_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|exactly|true|fair|i agree|that is true|that's true|good point)[.! ]*$",
    re.IGNORECASE,
)
AGREEMENT_START_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|exactly|i agree|that is fair|that's fair|that is true|that's true|good point|i like)\b",
    re.IGNORECASE,
)
COUNTER_POSITION_START_RE = re.compile(
    r"^\s*(?:i partly disagree|i disagree|i do not think|i don't think|i am not sure|"
    r"maybe,?\s+but|but|however)\b",
    re.IGNORECASE,
)
SHARED_REASONING_RE = re.compile(
    r"\b(?:we could|we can|we need|we should|our|proposal|route|solution|"
    r"test|overcome|avoid|what do we|how could we|how would|that changes|"
    r"good point|that is fair|then maybe)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MercerDecision:
    label: str
    confidence: float
    rationale: str


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text))


def has(pattern: re.Pattern[str], text: str) -> bool:
    return bool(pattern.search(text))


def references_previous(text: str, previous_text: str) -> bool:
    if not previous_text:
        return False
    lower = text.lower().strip()
    return lower.startswith(
        (
            "that ",
            "this ",
            "then ",
            "so ",
            "yes",
            "but",
            "and ",
            "good point",
            "i see",
            "i like",
        )
    ) or "connects back" in lower


def choose_mercer_label(text: str, previous_participant_text: str = "") -> MercerDecision:
    """Heuristically classify a participant utterance using Mercer 2005 talk types."""
    utterance = clean(text)
    lower = utterance.lower()
    wc = word_count(utterance)

    has_agreement = has(AGREEMENT_RE, utterance)
    has_disagreement = has(DISAGREEMENT_RE, utterance)
    has_reasoning = has(REASONING_RE, utterance)
    has_coconstruction = has(COCONSTRUCTION_RE, utterance)
    asks_question = has(QUESTION_RE, utterance)
    refers_back = references_previous(utterance, previous_participant_text)
    starts_with_agreement = has(AGREEMENT_START_RE, utterance)
    starts_with_counter_position = has(COUNTER_POSITION_START_RE, utterance)
    has_shared_reasoning = has(SHARED_REASONING_RE, utterance)

    if has(SHORT_CONFIRMATION_RE, utterance):
        return MercerDecision("cumulative", 0.88, "short agreement or confirmation without added reasoning")

    if starts_with_agreement and not has_disagreement and not has_shared_reasoning:
        return MercerDecision(
            "cumulative",
            0.78,
            "accepts a prior contribution and mainly adds compatible information without critique",
        )

    if starts_with_counter_position and not has_shared_reasoning:
        if has_reasoning or has_coconstruction:
            return MercerDecision(
                "exploratory", 0.80,
                "opens with a counter-position but provides reasoning or a constructive alternative",
            )
        return MercerDecision(
            "disputational", 0.76,
            "opens with a counter-position that is not yet integrated into a shared proposal",
        )

    if has_disagreement and not (has_reasoning or has_coconstruction):
        return MercerDecision(
            "disputational",
            0.74,
            "states disagreement, resistance, or a competing position without developing a shared alternative",
        )

    if has_disagreement and (has_reasoning or has_coconstruction):
        return MercerDecision(
            "exploratory",
            0.86,
            "challenges or qualifies an idea while giving reasons or proposing a constructive refinement",
        )

    if "pepper" in lower and asks_question:
        return MercerDecision(
            "exploratory",
            0.72,
            "uses Pepper to request clarification or facilitation that advances the joint task",
        )

    if (has_reasoning and has_coconstruction) or (refers_back and (has_reasoning or has_coconstruction)):
        return MercerDecision(
            "exploratory",
            0.83,
            "uses reasoning and builds on the previous contribution to develop a shared idea",
        )

    if has_agreement:
        if has_reasoning or has_coconstruction or wc > 18:
            return MercerDecision(
                "exploratory",
                0.76,
                "begins from agreement but adds explanation, implications, or a development of the shared idea",
            )
        return MercerDecision("cumulative", 0.80, "mainly accepts or repeats a previous contribution")

    if has_reasoning or has_coconstruction:
        return MercerDecision("exploratory", 0.69, "adds reasons, consequences, examples, or a proposed refinement")

    return MercerDecision("cumulative", 0.58, "adds task-relevant content without clear critique or explicit reasoning")


def participant_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if clean(row.get("event_type")).lower() == PARTICIPANT_EVENT_TYPE
    ]


def label_transcript(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    if not rows:
        raise ValueError("Transcript is empty.")
    required = {"event_type", "speaker", "text"}
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"Transcript is missing required column(s): {', '.join(sorted(missing))}")

    participants = participant_rows(rows)
    if not participants:
        raise ValueError("No participant rows found in transcript.")

    labelled: list[dict[str, object]] = []
    previous_text = ""
    for row in participants:
        text = clean(row.get("text"))
        decision = choose_mercer_label(text, previous_text)
        labelled.append(
            {
                "session_id": row.get("session_id", ""),
                "group_id": row.get("group_id", ""),
                "conversation_id": row.get("conversation_id", ""),
                "sequence_index": row.get("sequence_index", ""),
                "timestamp": row.get("timestamp", ""),
                "speaker": row.get("speaker", ""),
                "phase": row.get("phase", ""),
                "robot_turn_index": row.get("robot_turn_index", ""),
                "triggered_robot": row.get("triggered_robot", ""),
                "text": text,
                "mercer_label": decision.label,
                "label_confidence": f"{decision.confidence:.2f}",
                "label_rationale": decision.rationale,
            }
        )
        previous_text = text
    return labelled


def summarize_distribution(labelled: Sequence[dict[str, object]], group_cols: Sequence[str]) -> list[dict[str, object]]:
    totals: dict[tuple[str, ...], int] = {}
    counts: dict[tuple[str, ...], int] = {}

    for row in labelled:
        group_key = tuple(clean(row.get(col)) for col in group_cols)
        label = clean(row.get("mercer_label"))
        totals[group_key] = totals.get(group_key, 0) + 1
        counts[group_key + (label,)] = counts.get(group_key + (label,), 0) + 1

    summary: list[dict[str, object]] = []
    all_group_keys = sorted(totals)
    for group_key in all_group_keys:
        total = totals[group_key]
        for label in MERCER_LABELS:
            count = counts.get(group_key + (label,), 0)
            row = {col: group_key[index] for index, col in enumerate(group_cols)}
            row.update(
                {
                    "mercer_label": label,
                    "count": count,
                    "percentage": round((count / total) * 100.0, 2) if total else 0.0,
                }
            )
            summary.append(row)
    return summary


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "arialbd.ttf" if bold else "arial.ttf",
        "Calibri.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, selected_font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=selected_font)
    return box[2] - box[0], box[3] - box[1]


def chart_rows(summary: Sequence[dict[str, object]], group_col: str | None) -> list[dict[str, object]]:
    rows = []
    for row in summary:
        label = clean(row.get("mercer_label"))
        axis_label = label if not group_col else f"{clean(row.get(group_col))}\n{label}"
        rows.append({**row, "_axis_label": axis_label})
    return rows


def draw_bar_chart(summary: Sequence[dict[str, object]], title: str, output_path: Path, group_col: str | None = None) -> None:
    rows = chart_rows(summary, group_col)
    if not rows:
        return

    width, height = 1500, 900
    margin_left, margin_right, margin_top, margin_bottom = 125, 70, 95, 220
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    title_font = font(34, bold=True)
    label_font = font(18)
    small_font = font(17)
    axis_font = font(20, bold=True)

    palette = {
        "disputational": "#c0392b",
        "cumulative": "#2e86c1",
        "exploratory": "#229954",
    }

    title_w, _ = text_size(draw, title, title_font)
    draw.text(((width - title_w) / 2, 28), title, fill="#111827", font=title_font)

    x0, y0 = margin_left, margin_top
    x1, y1 = width - margin_right, height - margin_bottom
    draw.line([(x0, y1), (x1, y1)], fill="#2f3a45", width=3)
    draw.line([(x0, y0), (x0, y1)], fill="#2f3a45", width=3)

    max_count = max(1, max(int(row["count"]) for row in rows))
    y_max = max_count + max(1, int(max_count * 0.15))
    for step in range(6):
        frac = step / 5
        y = y1 - frac * plot_height
        value = round(y_max * frac)
        draw.line([(x0 - 8, y), (x0, y)], fill="#2f3a45", width=2)
        draw.line([(x0, y), (x1, y)], fill="#e6e8eb", width=1)
        tick = str(value)
        tick_w, tick_h = text_size(draw, tick, small_font)
        draw.text((x0 - tick_w - 14, y - tick_h / 2), tick, fill="#374151", font=small_font)

    slot_width = plot_width / len(rows)
    bar_width = max(26, min(90, int(slot_width * 0.55)))
    for index, row in enumerate(rows):
        count = int(row["count"])
        label = clean(row["mercer_label"])
        center = x0 + slot_width * (index + 0.5)
        bar_height = (count / y_max) * plot_height
        bx0 = center - bar_width / 2
        bx1 = center + bar_width / 2
        by0 = y1 - bar_height
        draw.rounded_rectangle([bx0, by0, bx1, y1], radius=5, fill=palette.get(label, "#566573"))

        value_text = str(count)
        value_w, value_h = text_size(draw, value_text, small_font)
        draw.text((center - value_w / 2, max(y0, by0 - value_h - 8)), value_text, fill="#111827", font=small_font)

        y_text = y1 + 18
        for part in clean(row["_axis_label"]).split("\n"):
            part_w, part_h = text_size(draw, part, label_font)
            draw.text((center - part_w / 2, y_text), part, fill="#111827", font=label_font)
            y_text += part_h + 5

    y_label = "Utterance count"
    y_label_w, y_label_h = text_size(draw, y_label, axis_font)
    y_label_img = Image.new("RGBA", (y_label_w + 12, y_label_h + 12), (255, 255, 255, 0))
    y_label_draw = ImageDraw.Draw(y_label_img)
    y_label_draw.text((6, 6), y_label, fill="#1f2933", font=axis_font)
    rotated = y_label_img.rotate(90, expand=True)
    image.paste(rotated, (25, int(y0 + plot_height / 2 - rotated.height / 2)), rotated)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def save_outputs(transcript_path: Path, output_dir: Path) -> None:
    chart_dir = output_dir / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_dir.mkdir(parents=True, exist_ok=True)

    labelled = label_transcript(read_csv(transcript_path))
    overall = summarize_distribution(labelled, [])
    by_phase = summarize_distribution(labelled, ["phase"])
    by_speaker = summarize_distribution(labelled, ["speaker"])

    write_csv(output_dir / "mercer_labelled_utterances.csv", labelled)
    write_csv(output_dir / "mercer_distribution_overall.csv", overall)
    write_csv(output_dir / "mercer_distribution_by_phase.csv", by_phase)
    write_csv(output_dir / "mercer_distribution_by_speaker.csv", by_speaker)

    draw_bar_chart(overall, "Mercer Talk Type Distribution", chart_dir / "mercer_distribution_overall.png")
    draw_bar_chart(by_phase, "Mercer Talk Type Distribution by Phase", chart_dir / "mercer_distribution_by_phase.png", group_col="phase")
    draw_bar_chart(by_speaker, "Mercer Talk Type Distribution by Speaker", chart_dir / "mercer_distribution_by_speaker.png", group_col="speaker")

    manifest = {
        "input_transcript": str(transcript_path.resolve()),
        "output_dir": str(output_dir.resolve()),
        "participant_utterances_labelled": len(labelled),
        "generated_files": sorted(str(path.relative_to(output_dir)) for path in output_dir.rglob("*") if path.is_file()),
        "note": "Automatic heuristic labels. Review labels and rationales before treating them as final human-coded data.",
    }
    with (output_dir / "run_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Label participant utterances as Mercer 2005 disputational, cumulative, or exploratory talk and graph the distribution."
    )
    parser.add_argument("--transcript", default=str(DEFAULT_TRANSCRIPT), help="Input transcript CSV.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for CSVs and charts.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    save_outputs(Path(args.transcript), Path(args.output_dir))
    print(f"Wrote Mercer coding outputs to {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
