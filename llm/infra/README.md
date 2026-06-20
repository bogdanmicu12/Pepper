# Live Bridge

`lmstudio_minimal_bridge.py` runs the live Pepper elicitation workflow.

## Requirements

- Python 3 with `httpx`, `sounddevice`, `numpy`, `pandas`, and `Pillow`
  available where needed.
- LM Studio running an OpenAI-compatible chat endpoint at
  `http://127.0.0.1:1234/v1/chat/completions`.
- `DEEPGRAM_API_KEY` set in the shell for microphone transcription.
- Optional: Pepper reachable over NAOqi, or Python 2.7 available for
  `pepper/tts.py`.

## Commands

List audio devices:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --list-audio-devices
```

Run the live scheduled experiment:

```powershell
python llm\infra\lmstudio_minimal_bridge.py `
  --live --deepgram-live --pepper `
  --group-id G01 --theme-id T1 `
  --phase divergence `
  --elicitation-mode scheduled `
  --intervention-every 4 `
  --evaluation_elicitation
```

Use a fixed elicitation strategy:

```powershell
python llm\infra\lmstudio_minimal_bridge.py `
  --live --deepgram-live --pepper `
  --elicitation-mode perspective_shift
```

Transcribe one audio file and send it to LM Studio:

```powershell
python llm\infra\lmstudio_minimal_bridge.py `
  --deepgram-audio path\to\audio.wav `
  --deepgram-api-key your_key_here
```

## Logging

`logs/transcript.csv` is the structured transcript used by analysis.
`logs/logs.csv` is a readable block log for inspection.
