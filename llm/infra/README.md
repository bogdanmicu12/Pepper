# Minimal run/test guide

## Run right now

From workspace root:

```powershell
python infra/lmstudio_minimal_bridge.py --request infra/test_request.json
```

Expected result:

- `"source": "lmstudio"` when the model replies before timeout.
- `"source": "fallback"` if generation is slow or fails.
- `"fallback_reason"` explains exactly why fallback happened.

Run scripted two-person simulation:

```powershell
python infra/lmstudio_minimal_bridge.py --simulate infra/test_scenario_X.json
```

Run experiment-controlled intervention mode:

```powershell
python infra/lmstudio_minimal_bridge.py --intervene
```

Type participant lines one by one. Type `exit` to stop.

## Brainstorm quick-run audio fallback

For the timed Pepper brainstorm runner, the quick real-Pepper commands can prefer the Focusrite interface and fall back to the laptop/default microphone if Focusrite is not connected:

```powershell
python .\llm\infra\pepper_brainstorm.py --session dynamic --pepper --pepper-optional --deepgram-live --deepgram-api-key %DEEPGRAM_API_KEY% --audio-prefer-device-name Focusrite --audio-input-device 15 --audio-fallback-to-default-input --audio-fallback-channels 1 --audio-fallback-sample-rate 0 --audio-input-channels 2 --audio-sample-rate 48000
```

The same audio flags can be used with the setup 2 quick command. If Focusrite is found, Deepgram transcribes channel 1 and channel 2 separately. If the fallback activates, Deepgram uses the laptop/default mic and prints one combined transcript.

To reduce duplicate transcripts caused by one microphone bleeding into the other Focusrite channel, use the current channel filters:

```powershell
--audio-channel-min-peak 250 --audio-channel-relative-peak 0.25 --audio-channel-relative-rms 0.20
```

For direct Ethernet, Pepper's `169.254.x.x` IP may change. In `pepper_brainstorm.py`, prefer:

```powershell
--pepper-ip auto
```

The runner will scan `169.254.x.x:9559` and switch to the reachable NAOqi IP before TTS starts.

Setup 1 uses an assertive but slightly more patient timing profile. The speech-start threshold is raised so room noise is less likely to count as participant speech, and Pepper leaves a short beat for people to finish before joining. It no longer defers dynamic replies indefinitely. After participant input, Pepper can join after a short active-discussion pause, and a stuck speech gate is treated as noise after a brief grace period. The LLM sees a small recent-context window and must name or paraphrase the newest participant idea before adding Pepper's own mechanism, first step, metric, or stakeholder. Required structure announcements still speak: Pepper announces the start of divergence, the start of convergence, and the final-idea request even if the room is busy.

Setup 2 has shorter silence gates than setup 1. After an attention cue, Pepper continues after a brief finished-sentence pause, and at the final-plan moment it synthesizes after a few quiet seconds instead of waiting for a long timeout.

The full setup 1 and setup 2 commands are documented in:

```text
brainstorm_setups.md
```

In `--intervene` mode:

- You type `group_id` and `theme_id` at start.
- If `theme_id` is recognized (T1 or T2 from design/themes.json), theme text is loaded automatically.
- If `theme_id` is not recognized, you are prompted for theme text.
- Participants can speak uninterrupted for any number of turns.
- The robot only replies when you type `ROBOT`.
- Type `CHANGE` to switch divergence/convergence phase.
- Every 4th robot reply (within the current phase), the robot uses the next unused strategy from `design/counterbalancing_elicitation.csv`.
- Non-4th replies are context-only (no prompt-bank shaping).
- After all 3 phase strategies are used, remaining replies stay context-only until phase changes.

## If you still see fallback

Check `fallback_reason` in the JSON output.

- `TimeoutError`: increase `timeout_seconds` in the request/session file.
- `Unusable LM Studio reply`: the model returned reasoning text instead of a facilitator line.

For `google/gemma-3-1b-it`, this can happen if reasoning/thinking is enabled in LM Studio.
Disable thinking/reasoning mode in the LM Studio model settings, then run the same command again.

## Public repository notes

The repository intentionally does not track local runtimes, downloaded ASR models, generated tablet pages, transcript logs, or API keys. Keep these local:

- `.tools/`
- `llm/asr_models/`
- `llm/logs/`
- `llm/tablet/*.html`
- `.env` or any file containing API keys

## LM Studio settings

- Endpoint: `http://127.0.0.1:1234/v1/chat/completions`
- Model: `google/gemma-3-1b-it`
- Temperature: `0.30-0.40`
- Timeout in request/session files: start with `30.0`, then lower until acceptable latency/fallback tradeoff.

## Logging format

The bridge writes `logs/logs.csv` in readable blocks instead of one compact CSV row.

Each conversation starts with:

```text
session_id,group_id,conversation_id
```

Each turn is stored as three lines:

```text
timestamp,phase,strategy,prompt_id,,prompt_text
participant_message_timestamp,participant_message
robot_reply_timestamp,robot_reply
```

Blank lines separate turns and conversations.
