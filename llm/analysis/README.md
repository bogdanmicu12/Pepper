# Analysis

`generate_graphs.py` turns live transcript logs and manual window coding into
CSV summaries and eight PNG charts.

## Inputs

- `--transcript`: structured `logs/transcript.csv` from the live bridge.
- `--interventions`: usually the same `logs/transcript.csv`; robot rows define
  elicitation windows.
- `--manual-measures`: manually coded window measures with idea fluency,
  elaboration units, and same-subject turn counts.
- `--output-dir`: destination for generated CSVs, charts, and manifest.

The live transcript already contains prompt IDs, strategies, phases, timestamps,
evaluation rows, and nonverbal event metadata. Manual coding should use
`window_id = session_id__prompt_id`.

## Run

```powershell
python llm\analysis\generate_graphs.py `
  --transcript llm\logs\transcript.csv `
  --interventions llm\logs\transcript.csv `
  --manual-measures llm\analysis\data\manual_window_measures.csv `
  --output-dir llm\analysis\outputs
```

Write blank/example templates:

```powershell
python llm\analysis\generate_graphs.py --write-templates llm\analysis\templates
```

## Elicitation Windows

An elicitation window starts when Pepper finishes a prompt-bank elicitation
prompt. It ends at the first matching boundary: the next elicitation prompt, an
explicit `window_end_time`, a phase change, session end, or the four-robot-turn
cap used by the live protocol.

## Chart Outputs

Generated PNGs are written to `--output-dir\charts`. The script keeps this set
limited to:

- `elicitation_engagement_by_phase_strategy.png`
- `connection_cue_rate_by_phase_strategy.png`
- `response_delay_by_phase_strategy.png`
- `speaking_time_by_phase_strategy.png`
- `vocal_activation_by_phase_strategy.png`
- `idea_fluency_by_phase_strategy.png`
- `elaboration_units_by_phase_strategy.png`
- `consecutive_topic_turns_by_phase_strategy.png`

CSV summaries and `run_manifest.json` are written directly in `--output-dir`.
