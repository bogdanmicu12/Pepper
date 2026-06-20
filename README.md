# Pepper Elicitation Project

Code for running Pepper-supported group ideation sessions and analysing the
resulting transcript logs.

## Layout

- `llm/infra/lmstudio_minimal_bridge.py`: live bridge between microphone input,
  LM Studio, optional Pepper TTS, and CSV logging.
- `llm/prompts/prompt_bank.csv`: elicitation prompt bank.
- `llm/design/counterbalancing_elicitation.csv`: group/theme/phase strategy
  schedule.
- `llm/design/themes.json`: experiment themes.
- `llm/logs/`: readable logs and structured transcript logs.
- `llm/analysis/generate_graphs.py`: analysis summaries and chart generation.
- `llm/analysis/data/manual_window_measures.csv`: manually coded window
  measures.
- `pepper/tts.py`: Python 2.7 NAOqi TTS relay for Pepper.

## Local Setup

Run commands from the repository root. Use Python 3.13 or another Python 3
environment with `pandas`, `numpy`, `Pillow`, `httpx`, and audio dependencies
installed.

Set your Deepgram key locally before microphone runs:

```powershell
$env:DEEPGRAM_API_KEY = "your_key_here"
```

LM Studio should expose an OpenAI-compatible endpoint at:

```text
http://127.0.0.1:1234/v1/chat/completions
```

## Run A Session

```powershell
python llm\infra\lmstudio_minimal_bridge.py `
  --live --deepgram-live --pepper `
  --group-id G01 --theme-id T1 `
  --phase divergence `
  --elicitation-mode scheduled `
  --intervention-every 4 `
  --evaluation_elicitation
```

Useful typed controls during a run:

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

Outputs:

- `llm/logs/transcript.csv`: structured transcript and evaluation events.
- `llm/logs/logs.csv`: readable session blocks.

## Generate Analysis Outputs

```powershell
python llm\analysis\generate_graphs.py `
  --transcript llm\logs\transcript.csv `
  --interventions llm\logs\transcript.csv `
  --manual-measures llm\analysis\data\manual_window_measures.csv `
  --output-dir llm\analysis\outputs
```

The chart output folder contains exactly these eight PNG files when the required
input data is available:

- `elicitation_engagement_by_phase_strategy.png`
- `connection_cue_rate_by_phase_strategy.png`
- `response_delay_by_phase_strategy.png`
- `speaking_time_by_phase_strategy.png`
- `vocal_activation_by_phase_strategy.png`
- `idea_fluency_by_phase_strategy.png`
- `elaboration_units_by_phase_strategy.png`
- `consecutive_topic_turns_by_phase_strategy.png`

CSV summaries and `run_manifest.json` are written next to the `charts` folder.
