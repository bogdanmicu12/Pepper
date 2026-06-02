# Mercer Coding

This folder contains a reproducible heuristic coder for Mercer talk types:

- `disputational`: disagreement or unresolved individual positions.
- `cumulative`: uncritical agreement, repetition, or simple additive confirmation.
- `exploratory`: critical, reasoned, co-constructive exchange.

The script labels participant utterances only. Pepper rows are excluded from
Mercer labels because Mercer coding is intended here for the two participants'
collaborative talk.

The coder is conservative: if a statement does not explicitly show
disputational, cumulative, or exploratory talk, `mercer_label` is left blank.
Robot-addressing, greetings, short task-management turns, and unclear fragments
are usually left unlabelled.

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

TXT conversation transcripts with `[PARTICIPANT_1]: ...` style speaker tags are
also supported:

```powershell
python "llm\Mercer coding\label_mercer_and_graph.py" `
  --transcript "llm\logs\conversations\Proactive_Assertive\MVI_0074.txt" `
  --output-dir "llm\Mercer coding\outputs\MVI_0074"
```

## Outputs

The script writes:

- `outputs/mercer_labelled_utterances.csv`
- `outputs/mercer_unlabelled_utterances.csv`
- `outputs/mercer_distribution_overall.csv`
- `outputs/mercer_distribution_by_phase.csv`
- `outputs/mercer_distribution_by_speaker.csv`
- `outputs/charts/mercer_distribution_overall.png`
- `outputs/charts/mercer_distribution_by_phase.png`
- `outputs/charts/mercer_distribution_by_speaker.png`
- `outputs/run_manifest.json`

The labels are automatic conservative heuristic labels. Use the rationale and
confidence columns to review and adjust labels before using them as final
research coding.
