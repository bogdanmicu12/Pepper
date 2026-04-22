# Minimal run/test guide

## Run right now

From workspace root:

```powershell
C:/Users/bogda/AppData/Local/Programs/Python/Python313/python.exe infra/lmstudio_minimal_bridge.py --request infra/example_request.json
```

Expected result:

- `"source": "lmstudio"` when the model replies before timeout.
- `"source": "fallback"` if generation is slow or fails.
- `"fallback_reason"` explains exactly why fallback happened.

Run scripted two-person simulation:

```powershell
C:/Users/bogda/AppData/Local/Programs/Python/Python313/python.exe infra/lmstudio_minimal_bridge.py --simulate infra/example_session.json
```

Run live console mode:

```powershell
C:/Users/bogda/AppData/Local/Programs/Python/Python313/python.exe infra/lmstudio_minimal_bridge.py --interactive
```

Type participant lines one by one. Type `exit` to stop.

## If you still see fallback

Check `fallback_reason` in the JSON output.

- `TimeoutError`: increase `timeout_seconds` in the request/session file.
- `Unusable LM Studio reply`: the model returned reasoning text instead of a facilitator line.

For `google/gemma-4-e4b`, this can happen if reasoning/thinking is enabled in LM Studio.
Disable thinking/reasoning mode in the LM Studio model settings, then run the same command again.

## LM Studio settings

- Endpoint: `http://127.0.0.1:1234/v1/chat/completions`
- Model: `google/gemma-4-e4b`
- Temperature: `0.30-0.40`
- Max tokens: `24-40`
- Timeout in request/session files: start with `3.0`, then lower until acceptable latency/fallback tradeoff.

## Themes

- First-year transition and belonging.
	Anchor: Tinto (1993), Strayhorn (2012).
- Student wellbeing and workload support.
	Anchor: broad student wellbeing and engagement research (O'Brien et al., 2018).