# LLM Bridge

This folder contains the prompt bank, counterbalancing schedule, live bridge,
logs, and analysis script used for Pepper-supported elicitation sessions.

## Live Run

From the repository root:

```powershell
python llm\infra\lmstudio_minimal_bridge.py `
  --live --deepgram-live --pepper `
  --group-id G01 --theme-id T1 `
  --phase divergence `
  --elicitation-mode scheduled `
  --intervention-every 4 `
  --evaluation_elicitation
```

Set `DEEPGRAM_API_KEY` in the shell, or pass `--deepgram-api-key`. LM Studio is
expected at `http://127.0.0.1:1234/v1/chat/completions` unless overridden in the
request payload.

The scheduled elicitation strategies are:

- `generative`
- `elaboration_evidence`
- `perspective_shift`

`llm/design/counterbalancing_elicitation.csv` determines the strategy order for
each `group_id`, `theme_id`, and phase. `llm/prompts/prompt_bank.csv` provides
the prompt text for each strategy and phase.

## Live Controls

Typed controls remain available while microphones run:

```text
ROBOT
SCORE
WINDOW
CHANGE
DIVERGENCE
CONVERGENCE
ELICITATION off|scheduled|perspective_shift|generative|elaboration_evidence
STYLE off|passive|assertive|supportive
INITIATIVE off|reactive|proactive
ROLE off|facilitator|solutionist
GROUP G01
THEME T1
exit
```

In reactive mode, saying `Pepper` or the common speech-to-text rendering
`Paper` triggers the same intervention as `ROBOT`.

## Logs

- `llm/logs/transcript.csv` is the structured analysis transcript. It contains
  participant utterances, robot turns, evaluation rows, nonverbal audio events,
  timestamps, prompt IDs, strategy labels, trigger metadata, and audio channel
  metadata.
- `llm/logs/logs.csv` is a readable block log with one session header and one
  prompt/participant/reply block per robot turn.

## Analysis

```powershell
python llm\analysis\generate_graphs.py `
  --transcript llm\logs\transcript.csv `
  --interventions llm\logs\transcript.csv `
  --manual-measures llm\analysis\data\manual_window_measures.csv `
  --output-dir llm\analysis\outputs
```

The analysis script writes CSV summaries, a manifest, and eight PNG charts in
`llm/analysis/outputs/charts`.
