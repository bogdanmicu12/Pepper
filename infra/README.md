# Minimal Infrastructure

## What This Does
- Reads one JSON request.
- Combines modular teammate modes.
- Calls LM Studio local server.
- Returns one short facilitation line.
- Falls back instantly if server is slow.

## Run
```powershell
python infra/lmstudio_minimal_bridge.py --request infra/example_request.json
```

## Input File
Use `infra/example_request.json` as template.

## Output
Single-line JSON:
- `ok`
- `source` (`lmstudio` or `fallback`)
- `reply`

## Pepper Wiring Later
Open `infra/lmstudio_minimal_bridge.py` and implement the commented stub functions:
- `receive_from_pepper`
- `send_to_pepper`
