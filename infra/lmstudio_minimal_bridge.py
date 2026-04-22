#!/usr/bin/env python3
import argparse
import json
import pathlib
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_json(path: pathlib.Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_modules():
    return {
        "registry": load_json(ROOT / "config" / "mode_registry.json"),
        "elicitation": load_json(ROOT / "prompts" / "modules" / "elicitation.json"),
        "style": load_json(ROOT / "prompts" / "modules" / "style.json"),
        "initiative": load_json(ROOT / "prompts" / "modules" / "initiative.json"),
    }


def get_mode_value(payload, modules, key):
    defaults = modules["registry"]["defaults"]
    selected = payload.get("mode_combo", {}).get(key, defaults[key])
    if selected not in modules["registry"]["dimensions"][key]:
        raise ValueError(f"Invalid mode for {key}: {selected}")
    return selected


def build_messages(payload, modules):
    elicitation_key = get_mode_value(payload, modules, "elicitation")
    style_key = get_mode_value(payload, modules, "style")
    initiative_key = get_mode_value(payload, modules, "initiative")

    system_text = (
        "You are Pepper, a neutral brainstorming facilitator. "
        "Output exactly one sentence, max 18 words. "
        "Never generate full solutions. Never judge participants. "
        f"Elicitation mode: {modules['elicitation'][elicitation_key]} "
        f"Style mode: {modules['style'][style_key]} "
        f"Initiative mode: {modules['initiative'][initiative_key]}"
    )

    user_text = (
        f"Theme: {payload.get('theme', '')}\n"
        f"Phase: {payload.get('phase', '')}\n"
        f"Last participant utterance: {payload.get('last_user_utterance', '')}\n"
        "Return one facilitation prompt line only."
    )

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def call_lmstudio(payload, messages):
    body = {
        "model": payload.get("model", "google/gemma-4-e4b"),
        "messages": messages,
        "temperature": payload.get("temperature", 0.4),
        "max_tokens": payload.get("max_tokens", 36),
    }

    request = urllib.request.Request(
        payload.get("server_url", "http://127.0.0.1:1234/v1/chat/completions"),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    timeout = float(payload.get("timeout_seconds", 1.2))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))
    return decoded["choices"][0]["message"]["content"].strip()


def process(payload):
    modules = load_modules()
    messages = build_messages(payload, modules)
    fallback = payload.get("fallback_prompt", "Could you make that idea more concrete?")

    try:
        reply = call_lmstudio(payload, messages)
        if not reply:
            return {"ok": True, "source": "fallback", "reply": fallback}
        return {"ok": True, "source": "lmstudio", "reply": reply}
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        return {"ok": True, "source": "fallback", "reply": fallback}


# --- Pepper Integration Stubs (commented on purpose) ---
# def receive_from_pepper():
#     """Replace with NAOqi/socket/REST input from Pepper side."""
#     pass
#
# def send_to_pepper(text: str):
#     """Replace with Pepper TTS or message bus output."""
#     pass
# --------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Minimal LM Studio bridge for modular facilitation modes")
    parser.add_argument("--request", required=True, help="Path to JSON request payload")
    args = parser.parse_args()

    payload_path = pathlib.Path(args.request)
    payload = load_json(payload_path)
    result = process(payload)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
