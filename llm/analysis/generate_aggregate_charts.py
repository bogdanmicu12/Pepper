from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover - convenience script
    raise SystemExit("This helper needs matplotlib.") from exc


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "llm" / "analysis" / "outputs" / "aggregate_chart_alternatives"
SOURCE_DIR = ROOT / "llm" / "analysis" / "outputs"

PHASES = ["divergence", "convergence"]
PHASE_LABELS = {"divergence": "Divergence", "convergence": "Convergence"}
PHASE_SHORT = {"divergence": "Div.", "convergence": "Conv."}
PHASE_COLORS = {"divergence": "#0072B2", "convergence": "#D55E00"}

STRATEGIES = ["generative", "elaboration_evidence", "perspective_shift"]
STRATEGY_LABELS = {
    "generative": "Generative",
    "elaboration_evidence": "Elaborative",
    "perspective_shift": "Perspective",
}
STRATEGY_COLORS = {
    "generative": "#3B6EA8",
    "elaboration_evidence": "#5B8E2D",
    "perspective_shift": "#C17D11",
}

DOMAIN_TITLES = {
    "engagement": "Engagement Aggregate",
    "substantive": "Substantive Contribution Aggregate",
}

METRIC_SPECS = {
    "engagement": [
        (
            "elicitation_engagement_score",
            "Self-reported engagement",
            "1-100 score",
            "elicitation_engagement_summary_by_phase_strategy.csv",
        ),
        (
            "connection_cue_rate",
            "Connection cue rate",
            "cues/min speaking",
            "transcript_summary_by_phase_strategy.csv",
        ),
        (
            "response_delay_seconds",
            "Response delay",
            "seconds",
            "transcript_summary_by_phase_strategy.csv",
        ),
        (
            "participant_speaking_time_seconds",
            "Speaking time",
            "seconds",
            "transcript_summary_by_phase_strategy.csv",
        ),
        (
            "vocal_activation_score",
            "Vocal activation",
            "0-100 index",
            "transcript_summary_by_phase_strategy.csv",
        ),
    ],
    "substantive": [
        (
            "idea_fluency",
            "Idea count",
            "distinct ideas",
            "manual_summary_by_phase_strategy.csv",
        ),
        (
            "elaboration_units",
            "Elaboration units",
            "units",
            "manual_summary_by_phase_strategy.csv",
        ),
        (
            "consecutive_topic_turns",
            "Same-subject turns",
            "turns",
            "manual_summary_by_phase_strategy.csv",
        ),
    ],
}

DESIGN_NAMES = {
    1: "Horizontal forest facets",
    2: "Paired horizontal bars",
    3: "Phase-column forest",
    4: "Strategy-column mini facets",
    5: "Phase dumbbell forest",
    6: "Row-normalized value heatmap",
    7: "Compressed indexed strips",
    8: "Strategy profile panels",
    9: "Ranked lollipop facets",
    10: "Metric-by-strategy mini table",
}


def setup_matplotlib() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#CBD5E1",
            "axes.labelcolor": "#334155",
            "axes.titlecolor": "#111827",
            "font.size": 10,
            "savefig.facecolor": "white",
            "savefig.bbox": "tight",
        }
    )


def read_metric_data() -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for specs in METRIC_SPECS.values():
        for _, _, _, filename in specs:
            path = SOURCE_DIR / filename
            if path.exists() and filename not in frames:
                frames[filename] = pd.read_csv(path)
    return frames


def metric_rows(domain: str, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for metric, label, unit, filename in METRIC_SPECS[domain]:
        frame = frames.get(filename)
        if frame is None or frame.empty:
            continue
        mean_col = f"{metric}_mean"
        std_col = f"{metric}_std"
        count_col = f"{metric}_count"
        required = {"phase", "strategy", mean_col}
        if not required.issubset(frame.columns):
            continue
        for _, row in frame.iterrows():
            phase = str(row.get("phase", "")).strip().lower()
            strategy = str(row.get("strategy", "")).strip().lower()
            if phase not in PHASES or strategy not in STRATEGIES:
                continue
            mean = pd.to_numeric(pd.Series([row.get(mean_col)]), errors="coerce").iloc[0]
            if pd.isna(mean):
                continue
            std = pd.to_numeric(pd.Series([row.get(std_col)]), errors="coerce").iloc[0] if std_col in frame else np.nan
            count = pd.to_numeric(pd.Series([row.get(count_col)]), errors="coerce").iloc[0] if count_col in frame else np.nan
            ci95 = 0.0
            if pd.notna(std) and pd.notna(count) and float(count) > 1:
                ci95 = 1.96 * float(std) / math.sqrt(float(count))
            rows.append(
                {
                    "metric": metric,
                    "label": label,
                    "unit": unit,
                    "phase": phase,
                    "strategy": strategy,
                    "mean": float(mean),
                    "ci95": float(ci95),
                }
            )
    return pd.DataFrame(rows)


def metric_limits(data: pd.DataFrame, metric: str) -> tuple[float, float]:
    subset = data[data["metric"].eq(metric)]
    if subset.empty:
        return 0.0, 1.0
    low = float((subset["mean"] - subset["ci95"]).min())
    high = float((subset["mean"] + subset["ci95"]).max())
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        return 0.0, max(1.0, high)
    pad = max((high - low) * 0.15, 0.05)
    return max(0.0, low - pad), high + pad


def save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300)
    plt.close(fig)


def draw_design_1(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, axes = plt.subplots(len(metrics), 1, figsize=(9.5, 2.35 * len(metrics)), sharex=False)
    axes = np.atleast_1d(axes)
    fig.suptitle(f"{DOMAIN_TITLES[domain]}: horizontal forest facets", fontsize=16, fontweight="bold")
    for ax, metric in zip(axes, metrics):
        subset = data[data["metric"].eq(metric)]
        x_min, x_max = metric_limits(data, metric)
        y_positions = np.arange(len(STRATEGIES))
        for phase, offset in [("divergence", -0.12), ("convergence", 0.12)]:
            phase_rows = subset[subset["phase"].eq(phase)].set_index("strategy")
            means = [phase_rows.loc[s, "mean"] if s in phase_rows.index else np.nan for s in STRATEGIES]
            ci = [phase_rows.loc[s, "ci95"] if s in phase_rows.index else 0 for s in STRATEGIES]
            ax.errorbar(
                means,
                y_positions + offset,
                xerr=ci,
                fmt="o",
                color=PHASE_COLORS[phase],
                label=PHASE_LABELS[phase],
                capsize=3,
            )
        ax.set_xlim(x_min, x_max)
        ax.set_yticks(y_positions, [STRATEGY_LABELS[s] for s in STRATEGIES])
        ax.set_title(str(subset["label"].iloc[0]))
        ax.grid(axis="x", color="#E2E8F0")
    axes[0].legend(loc="lower right", frameon=False)
    save(fig, path)


def draw_design_2(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, axes = plt.subplots(len(metrics), 1, figsize=(10, 2.25 * len(metrics)))
    axes = np.atleast_1d(axes)
    fig.suptitle(f"{DOMAIN_TITLES[domain]}: paired horizontal bars", fontsize=16, fontweight="bold")
    for ax, metric in zip(axes, metrics):
        subset = data[data["metric"].eq(metric)]
        labels = []
        values = []
        colors = []
        for strategy in STRATEGIES:
            for phase in PHASES:
                row = subset[subset["phase"].eq(phase) & subset["strategy"].eq(strategy)]
                labels.append(f"{STRATEGY_LABELS[strategy]} / {PHASE_SHORT[phase]}")
                values.append(float(row["mean"].iloc[0]) if not row.empty else np.nan)
                colors.append(PHASE_COLORS[phase])
        y = np.arange(len(labels))
        ax.barh(y, values, color=colors, alpha=0.88)
        ax.set_yticks(y, labels)
        ax.invert_yaxis()
        ax.set_title(str(subset["label"].iloc[0]))
        ax.grid(axis="x", color="#E2E8F0")
    save(fig, path)


def draw_design_3(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, axes = plt.subplots(len(metrics), 2, figsize=(11, 2.35 * len(metrics)))
    fig.suptitle(f"{DOMAIN_TITLES[domain]}: phase-column forest", fontsize=16, fontweight="bold")
    for row_idx, metric in enumerate(metrics):
        subset = data[data["metric"].eq(metric)]
        x_min, x_max = metric_limits(data, metric)
        for col_idx, phase in enumerate(PHASES):
            ax = axes[row_idx, col_idx] if len(metrics) > 1 else axes[col_idx]
            phase_rows = subset[subset["phase"].eq(phase)].set_index("strategy")
            y = np.arange(len(STRATEGIES))
            means = [phase_rows.loc[s, "mean"] if s in phase_rows.index else np.nan for s in STRATEGIES]
            ci = [phase_rows.loc[s, "ci95"] if s in phase_rows.index else 0 for s in STRATEGIES]
            ax.errorbar(means, y, xerr=ci, fmt="o", color=PHASE_COLORS[phase], capsize=3)
            ax.set_xlim(x_min, x_max)
            ax.set_yticks(y, [STRATEGY_LABELS[s] for s in STRATEGIES])
            ax.set_title(f"{subset['label'].iloc[0]} - {PHASE_LABELS[phase]}")
            ax.grid(axis="x", color="#E2E8F0")
    save(fig, path)


def draw_design_4(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, axes = plt.subplots(len(metrics), 3, figsize=(12, 2.2 * len(metrics)))
    fig.suptitle(f"{DOMAIN_TITLES[domain]}: strategy-column mini facets", fontsize=16, fontweight="bold")
    for row_idx, metric in enumerate(metrics):
        subset = data[data["metric"].eq(metric)]
        x_min, x_max = metric_limits(data, metric)
        for col_idx, strategy in enumerate(STRATEGIES):
            ax = axes[row_idx, col_idx] if len(metrics) > 1 else axes[col_idx]
            strat_rows = subset[subset["strategy"].eq(strategy)].set_index("phase")
            y = np.arange(len(PHASES))
            means = [strat_rows.loc[p, "mean"] if p in strat_rows.index else np.nan for p in PHASES]
            ci = [strat_rows.loc[p, "ci95"] if p in strat_rows.index else 0 for p in PHASES]
            ax.errorbar(means, y, xerr=ci, fmt="o", color=STRATEGY_COLORS[strategy], capsize=3)
            ax.set_xlim(x_min, x_max)
            ax.set_yticks(y, [PHASE_LABELS[p] for p in PHASES])
            ax.set_title(f"{STRATEGY_LABELS[strategy]} - {subset['label'].iloc[0]}")
            ax.grid(axis="x", color="#E2E8F0")
    save(fig, path)


def draw_design_5(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, axes = plt.subplots(len(metrics), 1, figsize=(10, 2.35 * len(metrics)))
    axes = np.atleast_1d(axes)
    fig.suptitle(f"{DOMAIN_TITLES[domain]}: phase dumbbell forest", fontsize=16, fontweight="bold")
    for ax, metric in zip(axes, metrics):
        subset = data[data["metric"].eq(metric)]
        x_min, x_max = metric_limits(data, metric)
        for y, strategy in enumerate(STRATEGIES):
            rows = subset[subset["strategy"].eq(strategy)].set_index("phase")
            if not set(PHASES).issubset(rows.index):
                continue
            x1 = float(rows.loc["divergence", "mean"])
            x2 = float(rows.loc["convergence", "mean"])
            ax.plot([x1, x2], [y, y], color="#94A3B8", linewidth=2)
            ax.scatter([x1], [y], color=PHASE_COLORS["divergence"], s=55)
            ax.scatter([x2], [y], color=PHASE_COLORS["convergence"], s=55)
        ax.set_xlim(x_min, x_max)
        ax.set_yticks(np.arange(len(STRATEGIES)), [STRATEGY_LABELS[s] for s in STRATEGIES])
        ax.set_title(str(subset["label"].iloc[0]))
        ax.grid(axis="x", color="#E2E8F0")
    save(fig, path)


def draw_design_6(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    labels = []
    values = []
    for metric in metrics:
        subset = data[data["metric"].eq(metric)]
        low = float(subset["mean"].min())
        high = float(subset["mean"].max())
        denom = max(high - low, 1e-9)
        for strategy in STRATEGIES:
            for phase in PHASES:
                row = subset[subset["strategy"].eq(strategy) & subset["phase"].eq(phase)]
                labels.append(f"{subset['label'].iloc[0]}\n{STRATEGY_LABELS[strategy]} {PHASE_SHORT[phase]}")
                values.append((float(row["mean"].iloc[0]) - low) / denom if not row.empty else np.nan)
    fig, ax = plt.subplots(figsize=(12, max(5, len(values) * 0.22)))
    matrix = np.array(values).reshape(len(values), 1)
    ax.imshow(matrix, aspect="auto", cmap="Blues", vmin=0, vmax=1)
    ax.set_yticks(np.arange(len(labels)), labels)
    ax.set_xticks([])
    ax.set_title(f"{DOMAIN_TITLES[domain]}: row-normalized value heatmap", fontsize=16, fontweight="bold")
    save(fig, path)


def draw_design_7(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, ax = plt.subplots(figsize=(12, max(5.5, len(metrics) * 1.4)))
    ax.set_title(f"{DOMAIN_TITLES[domain]}: compressed indexed strips", fontsize=16, fontweight="bold")
    y_base = 0
    yticks = []
    ylabels = []
    for metric in metrics:
        subset = data[data["metric"].eq(metric)]
        low = float(subset["mean"].min())
        high = float(subset["mean"].max())
        denom = max(high - low, 1e-9)
        for i, strategy in enumerate(STRATEGIES):
            for phase in PHASES:
                row = subset[subset["strategy"].eq(strategy) & subset["phase"].eq(phase)]
                if row.empty:
                    continue
                x = (float(row["mean"].iloc[0]) - low) / denom
                y = y_base + i
                ax.scatter(x, y, s=75, color=PHASE_COLORS[phase])
            yticks.append(y_base + i)
            ylabels.append(f"{subset['label'].iloc[0]} / {STRATEGY_LABELS[strategy]}")
        y_base += len(STRATEGIES) + 1
    ax.set_xlim(-0.05, 1.05)
    ax.set_yticks(yticks, ylabels)
    ax.grid(axis="x", color="#E2E8F0")
    save(fig, path)


def draw_design_8(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.8), sharey=True)
    fig.suptitle(f"{DOMAIN_TITLES[domain]}: strategy profile panels", fontsize=16, fontweight="bold")
    for ax, strategy in zip(axes, STRATEGIES):
        labels = []
        divergence = []
        convergence = []
        for metric in metrics:
            subset = data[data["metric"].eq(metric)]
            low = float(subset["mean"].min())
            high = float(subset["mean"].max())
            denom = max(high - low, 1e-9)
            labels.append(str(subset["label"].iloc[0]))
            for phase, target in [("divergence", divergence), ("convergence", convergence)]:
                row = subset[subset["strategy"].eq(strategy) & subset["phase"].eq(phase)]
                target.append((float(row["mean"].iloc[0]) - low) / denom if not row.empty else np.nan)
        y = np.arange(len(labels))
        ax.plot(divergence, y, marker="o", color=PHASE_COLORS["divergence"], label="Divergence")
        ax.plot(convergence, y, marker="o", color=PHASE_COLORS["convergence"], label="Convergence")
        ax.set_title(STRATEGY_LABELS[strategy])
        ax.set_xlim(0, 1)
        ax.set_yticks(y, labels)
        ax.grid(axis="x", color="#E2E8F0")
    axes[-1].legend(frameon=False)
    save(fig, path)


def draw_design_9(domain: str, data: pd.DataFrame, path: Path) -> None:
    plot_data = data.copy()
    plot_data["label_full"] = (
        plot_data["label"].astype(str)
        + " / "
        + plot_data["strategy"].map(STRATEGY_LABELS)
        + " / "
        + plot_data["phase"].map(PHASE_SHORT)
    )
    plot_data = plot_data.sort_values("mean")
    fig, ax = plt.subplots(figsize=(11, max(5, len(plot_data) * 0.24)))
    y = np.arange(len(plot_data))
    colors = plot_data["phase"].map(PHASE_COLORS).tolist()
    ax.hlines(y, 0, plot_data["mean"], color="#CBD5E1", linewidth=2)
    ax.scatter(plot_data["mean"], y, color=colors, s=50)
    ax.set_yticks(y, plot_data["label_full"])
    ax.set_title(f"{DOMAIN_TITLES[domain]}: ranked lollipop facets", fontsize=16, fontweight="bold")
    ax.grid(axis="x", color="#E2E8F0")
    save(fig, path)


def draw_design_10(domain: str, data: pd.DataFrame, path: Path) -> None:
    metrics = list(dict.fromkeys(data["metric"].tolist()))
    fig, ax = plt.subplots(figsize=(12, max(4.5, len(metrics) * 0.8)))
    ax.axis("off")
    ax.set_title(f"{DOMAIN_TITLES[domain]}: metric-by-strategy mini table", fontsize=16, fontweight="bold", pad=18)
    rows = []
    for metric in metrics:
        subset = data[data["metric"].eq(metric)]
        for phase in PHASES:
            row = [f"{subset['label'].iloc[0]} / {PHASE_LABELS[phase]}"]
            for strategy in STRATEGIES:
                value = subset[subset["phase"].eq(phase) & subset["strategy"].eq(strategy)]
                row.append(f"{float(value['mean'].iloc[0]):.2f}" if not value.empty else "")
            rows.append(row)
    table = ax.table(
        cellText=rows,
        colLabels=["Metric / phase"] + [STRATEGY_LABELS[s] for s in STRATEGIES],
        loc="center",
        cellLoc="center",
        colLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.35)
    save(fig, path)


DESIGN_DRAWERS = {
    1: draw_design_1,
    2: draw_design_2,
    3: draw_design_3,
    4: draw_design_4,
    5: draw_design_5,
    6: draw_design_6,
    7: draw_design_7,
    8: draw_design_8,
    9: draw_design_9,
    10: draw_design_10,
}


def main() -> int:
    setup_matplotlib()
    frames = read_metric_data()
    if not frames:
        raise SystemExit(f"No summary CSV files found in {SOURCE_DIR}")

    written: list[Path] = []
    for domain in ("engagement", "substantive"):
        data = metric_rows(domain, frames)
        if data.empty:
            continue
        for design_number, drawer in DESIGN_DRAWERS.items():
            safe_name = DESIGN_NAMES[design_number].lower().replace(" ", "_").replace("-", "_")
            path = OUTPUT_DIR / f"{domain}_{design_number:02d}_{safe_name}.png"
            drawer(domain, data, path)
            written.append(path)

    if not written:
        raise SystemExit("No aggregate alternatives could be drawn from the available summaries.")
    print(f"Wrote {len(written)} aggregate chart alternatives to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
