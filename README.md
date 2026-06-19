# Pepper Research Toolkit

This repository contains the local tooling for running Pepper-supported
brainstorming sessions and analysing the resulting conversations.

## Repository Layout

- `llm/infra/`: LM Studio bridge, live Deepgram input, Pepper TTS/ASR relay, and scripted test scenarios.
- `llm/design/`: experiment design files such as themes and counterbalancing schedules.
- `llm/prompts/`: prompt-bank data used by scheduled and fixed elicitation modes.
- `llm/Participation inequality/`: adjusted Gini analysis for transcript CSVs and exported `.txt` conversations.
- `llm/Mercer coding/`: heuristic Mercer talk-type coding and chart generation.
- `pepper/`: Python 2.7 NAOqi helper scripts used when the Python 3 bridge cannot import NAOqi directly.

## Common Commands

Run all commands from the repository root unless a README says otherwise.

```powershell
python llm\infra\lmstudio_minimal_bridge.py --request llm\infra\test_request.json
python llm\infra\lmstudio_minimal_bridge.py --simulate llm\infra\test_scenario_realistic.json
python llm\infra\lmstudio_minimal_bridge.py --live
```

Run live microphone input through Deepgram:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --group-id G01 --theme-id T1
```

Run live microphone input and speak Pepper replies:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --pepper --pepper-ip 192.168.1.110
```

Run analysis:

```powershell
python "llm\Participation inequality\run_adjusted_gini.py" --demo
python "llm\Participation inequality\run_condition_gini_from_txt.py"
python "llm\Mercer coding\label_mercer_and_graph.py" --condition "Proactive_Assertive"
```

## Environment Notes

- The main bridge runs on Python 3.
- Pepper NAOqi helpers run on Python 2.7.
- Copy `.env.example` to `.env` and set `DEEPGRAM_API_KEY` for live/audio
  transcription.
- Set `NAOQI_SDK_ROOT` in `.env` to your NAOqi SDK root, or use
  `NAOQI_PYTHONPATH` for the SDK `lib` folder.

More detailed command references are in [llm/README.md](llm/README.md),
[llm/infra/README.md](llm/infra/README.md),
[llm/Participation inequality/README.md](llm/Participation%20inequality/README.md),
and [llm/Mercer coding/README.md](llm/Mercer%20coding/README.md).
