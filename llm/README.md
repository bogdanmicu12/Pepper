# LLM And Experiment Tools

This folder contains the LM Studio bridge, prompt/design data, live audio
integration, and analysis utilities for Pepper brainstorming sessions.

## Main Bridge

Run these commands from the repository root.

One-turn request:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --request llm\infra\test_request.json
```

Scripted multi-turn simulation:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --simulate llm\infra\test_scenario_realistic.json
```

Manual intervention mode:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --intervene --group-id G01 --theme-id T1
```

Simple live typed conversation:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live
```

Live microphone input through Deepgram:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --group-id G01 --theme-id T1
```

Live Deepgram input with Pepper speech output:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --pepper --pepper-ip 192.168.1.110
```

List audio input devices:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --list-audio-devices
```

Transcribe one audio file and send it to LM Studio:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --deepgram-audio path\to\audio.wav
```

## Live Controls

Live and intervention modes support these settings:

- `--elicitation-mode off|scheduled|perspective_shift|constraint_reframing|generative|elaboration_evidence`
- `--style-mode off|passive|assertive|supportive`
- `--initiative off|reactive|proactive`
- `--role-mode off|facilitator|solutionist`
- `--group-id G01`
- `--theme-id T1`
- `--phase divergence|convergence`
- `--intervention-every 4`

While live keyboard controls are enabled, typed commands include `ROBOT`,
`CHANGE`, `DIVERGENCE`, `CONVERGENCE`, `ELICITATION ...`, `STYLE ...`,
`INITIATIVE ...`, `ROLE ...`, `GROUP ...`, `THEME ...`, and `exit`.

## Environment

- LM Studio endpoint defaults to `http://127.0.0.1:1234/v1/chat/completions`.
- Deepgram uses `DEEPGRAM_API_KEY` from `.env` or the shell unless
  `--deepgram-api-key` is passed.
- Pepper direct NAOqi access is attempted from Python 3 first.
- If Python 3 cannot import NAOqi, Pepper TTS/ASR falls back to the Python 2.7
  helpers in `pepper/tts.py` and `pepper/asr.py`.
- Set `NAOQI_SDK_ROOT` or `NAOQI_PYTHONPATH` when the SDK is not in a default
  location.

## Analysis Commands

Participation inequality from transcript CSVs:

```powershell
python "llm\Participation inequality\run_adjusted_gini.py" --demo
python "llm\Participation inequality\run_adjusted_gini.py" --transcript path\to\transcript.csv --dataset-name my_session
```

Participation inequality from exported condition folders:

```powershell
python "llm\Participation inequality\run_condition_gini_from_txt.py"
python "llm\Participation inequality\run_condition_gini_from_txt.py" --condition "Proactive_Assertive"
```

Mercer coding:

```powershell
python "llm\Mercer coding\label_mercer_and_graph.py"
python "llm\Mercer coding\label_mercer_and_graph.py" --condition "Proactive_Assertive"
```
