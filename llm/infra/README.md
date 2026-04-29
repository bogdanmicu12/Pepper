# Minimal run/test guide

## Run right now

From workspace root:

```powershell
C:/Users/bogda/AppData/Local/Programs/Python/Python313/python.exe infra/lmstudio_minimal_bridge.py --request infra/test_request.json
```

Expected result:

- `"source": "lmstudio"` when the model replies before timeout.
- `"source": "fallback"` if generation is slow or fails.
- `"fallback_reason"` explains exactly why fallback happened.

Run scripted two-person simulation:

```powershell
C:/Users/bogda/AppData/Local/Programs/Python/Python313/python.exe infra/lmstudio_minimal_bridge.py --simulate infra/test_scenario_X.json
```

Run experiment-controlled intervention mode:

```powershell
C:/Users/bogda/AppData/Local/Programs/Python/Python313/python.exe infra/lmstudio_minimal_bridge.py --intervene
```

Type participant lines one by one. Type `exit` to stop.

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