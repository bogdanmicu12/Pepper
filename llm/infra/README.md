# LM Studio Bridge

`lmstudio_minimal_bridge.py` connects experiment prompts, LM Studio, live
microphone input, Deepgram transcription, and Pepper output.

Run commands from the repository root.

## Quick Checks

One-turn request:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --request llm\infra\test_request.json
```

Scripted simulation:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --simulate llm\infra\test_scenario_realistic.json
```

List audio devices:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --list-audio-devices
```

Expected request output:

- `"source": "lmstudio"` when LM Studio responds before timeout.
- `"source": "fallback"` when generation times out, fails, or returns an unusable response.
- `"fallback_reason"` explains the failure.

## Live Modes

Typed live conversation:

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

Use the laptop/default microphone instead of Focusrite autodetection:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --audio-input-mode laptop
```

Force a specific audio device by index or name substring:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --audio-device Focusrite
```

## Experiment Controls

Manual intervention mode:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --intervene --group-id G01 --theme-id T1
```

Scheduled live elicitation:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --pepper --group-id G01 --theme-id T1 --phase divergence --elicitation-mode scheduled --intervention-every 4
```

Fixed elicitation/style example:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --pepper --elicitation-mode perspective_shift --style-mode assertive --initiative proactive
```

Disable experiment shaping:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --deepgram-live --pepper --elicitation-mode off --style-mode off --initiative off
```

Add `--evaluation-elicitation` to ask the researcher for 1-100 engagement and
creative-confidence ratings during scheduled/fixed elicitation runs.

## Deepgram Audio File Mode

```powershell
python llm\infra\lmstudio_minimal_bridge.py --deepgram-audio path\to\audio.wav
```

Override the Deepgram endpoint if needed:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --deepgram-audio path\to\audio.wav --deepgram-endpoint https://api.eu.deepgram.com/v1/listen
```

Deepgram reads `DEEPGRAM_API_KEY` from `.env` or the shell unless
`--deepgram-api-key` is passed.

## Pepper Helpers

The bridge tries direct Python 3 NAOqi access first. If unavailable, it uses the
Python 2.7 helpers:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --pepper --pepper-legacy-python "C:\Python27\python.exe"
```

Override helper paths if needed:

```powershell
python llm\infra\lmstudio_minimal_bridge.py --live --pepper --pepper-legacy-tts-script pepper\tts.py --pepper-legacy-asr-script pepper\asr.py
```

Set `NAOQI_SDK_ROOT` to the SDK root or `NAOQI_PYTHONPATH` to the SDK `lib`
folder when NAOqi is installed outside the default search locations.

## Logging

Readable turn logs are written to `llm/logs/logs.csv` by default.

Continuous microphone runs also write `llm/logs/transcript.csv`, with one row
per participant utterance, robot reply, warning, or error. Transcript rows
include session/group/conversation IDs, speaker, text, timestamps, audio device
metadata, trigger state, prompt/model metadata, and fallback reasons.

## LM Studio Settings

- Endpoint: `http://127.0.0.1:1234/v1/chat/completions`
- Model used in local testing: `google/gemma-3-1b-it`
- Suggested temperature: `0.30-0.40`
- Start with `timeout_seconds: 30.0` in request/session JSON files, then lower
  it after latency is acceptable.

If output falls back because the model returns reasoning text, disable
thinking/reasoning mode in LM Studio and rerun the same command.
