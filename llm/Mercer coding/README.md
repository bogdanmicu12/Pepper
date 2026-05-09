# Mercer Coding

This folder contains a reproducible heuristic coder for Mercer 2005 talk types:

- `disputational`: disagreement or unresolved individual positions.
- `cumulative`: uncritical agreement, repetition, or simple additive confirmation.
- `exploratory`: critical, reasoned, co-constructive exchange.

The script reads the synthetic TU Delft transcript and labels participant utterances only. Pepper rows are excluded from Mercer labels because Mercer coding is intended here for the two participants' collaborative talk. Participant rows that ask Pepper a question are still coded because they are participant utterances.

## Run

From the repository root:

```powershell
python "llm\Mercer coding\label_mercer_and_graph.py"
```

Optional custom paths:

```powershell
python "llm\Mercer coding\label_mercer_and_graph.py" `
  --transcript "llm\logs\synthetic_tu_delft_campus_experience\transcript.csv" `
  --output-dir "llm\Mercer coding\outputs"
```

## Outputs

The script writes:

- `outputs/mercer_labelled_utterances.csv`
- `outputs/mercer_distribution_overall.csv`
- `outputs/mercer_distribution_by_phase.csv`
- `outputs/mercer_distribution_by_speaker.csv`
- `outputs/charts/mercer_distribution_overall.png`
- `outputs/charts/mercer_distribution_by_phase.png`
- `outputs/charts/mercer_distribution_by_speaker.png`
- `outputs/run_manifest.json`

The labels are automatic heuristic labels. Use the rationale and confidence columns to review and adjust labels before using them as final research coding.
