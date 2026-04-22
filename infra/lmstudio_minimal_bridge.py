#!/usr/bin/env python3
import argparse
import csv
import json
import pathlib
import re
import time
import urllib.error
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPT_BANK = ROOT / "prompts" / "prompt_bank.csv"

MODE_REGISTRY = {
    "elicitation": ["perspective_shift", "constraint_reframing", "elaboration_evidence"],
    "style": ["passive", "assertive"],
    "initiative": ["reactive", "proactive"],
}

MODE_DEFAULTS = {
    "elicitation": "perspective_shift",
    "style": "passive",
    "initiative": "reactive",
}

STYLE_SETUP = {
    "passive": "Use a gentle, low-pressure, non-directive tone.",
    "assertive": "Use a concise and direct facilitation tone while staying neutral.",
}

INITIATIVE_SETUP = {
    "reactive": "Intervene only after a pause or a direct prompt from participants.",
    "proactive": "Intervene when momentum drops or when phase goals are drifting.",
}

ELICITATION_SETUP = {
    "perspective_shift": (
        "Use perspective-taking to broaden idea search before judgement."
    ),
    "constraint_reframing": (
        "Use realistic constraints to make ideas concrete without shutting creativity down."
    ),
    "elaboration_evidence": (
        "Push abstract ideas into concrete, testable details and evidence checks."
    ),
}

BASE_SETUP = (
    "You are Pepper, a neutral facilitator in a two-person brainstorming session. "
    "Never generate solutions or judge participants. "
    "Respond naturally and conversationally, as if speaking aloud. "
    "Keep responses brief (1-3 sentences) and focused on one key idea or question."
)


def load_json(path):
    # utf-8-sig also handles JSON files saved from PowerShell with BOM.
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_prompt_bank():
    with PROMPT_BANK.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def get_mode_value(payload, key):
    selected = payload.get("mode_combo", {}).get(key, MODE_DEFAULTS[key])
    if selected not in MODE_REGISTRY[key]:
        raise ValueError(f"Invalid mode for {key}: {selected}")
    return selected


def select_prompt(payload, prompt_bank):
    elicitation_key = get_mode_value(payload, "elicitation")
    phase = payload.get("phase", "divergence")
    requested = payload.get("prompt_id")

    if requested:
        for row in prompt_bank:
            if row["prompt_id"] == requested:
                return row, elicitation_key

    for row in prompt_bank:
        if row["strategy"] == elicitation_key and row["phase"] == phase:
            return row, elicitation_key

    raise ValueError(f"No prompt found for {elicitation_key} / {phase}")


def build_messages(payload, prompt_row, elicitation_key):
    style_key = get_mode_value(payload, "style")
    initiative_key = get_mode_value(payload, "initiative")
    seed_ideas = payload.get("seed_ideas", [])
    seed_text = "; ".join(seed_ideas) if seed_ideas else "none"

    system_text = (
        f"{BASE_SETUP} "
        f"Elicitation mode guidance: {ELICITATION_SETUP[elicitation_key]} "
        f"Style guidance: {STYLE_SETUP[style_key]} "
        f"Initiative guidance: {INITIATIVE_SETUP[initiative_key]}"
    )

    user_text = (
        f"Theme: {payload.get('theme', '')}\n"
        f"Phase: {payload.get('phase', '')}\n"
        f"Last participant utterance: {payload.get('last_user_utterance', '')}\n"
        f"Seed ideas: {seed_text}\n"
        f"Intervention to deliver: {prompt_row['text']}\n"
        "Respond naturally as if speaking in the conversation, not as meta-commentary. Keep your response brief and direct."
    )

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def call_lmstudio(payload, messages):
    body = {
        "model": payload.get("model", "google/gemma-4-e4b"),
        "messages": messages,
        "temperature": payload.get("temperature", 0.35),
        "max_tokens": payload.get("max_tokens", 60),
        "thinking": False,
        "enable_thinking": False,
    }

    request = urllib.request.Request(
        payload.get("server_url", "http://127.0.0.1:1234/v1/chat/completions"),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    timeout = float(payload.get("timeout_seconds", 5.0))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded = json.loads(response.read().decode("utf-8"))

    choice = decoded.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = (message.get("content") or "").strip()

    if not content:
        content = (message.get("reasoning_content") or "").strip()

    if not content:
        content = (choice.get("text") or "").strip()

    if not content:
        raise ValueError("Empty LM Studio reply")

    return content


def fallback_reply(payload, prompt_row):
    return payload.get("fallback_prompt") or prompt_row["text"]


def sanitize_reply(text):
    line = " ".join((text or "").strip().split())
    lower = line.lower()
    
    # Detect explicit reasoning markers
    reasoning_markers = [
        "thinking process",
        "analyze the request",
        "analyze the context",
        "analyze role",
        "internal reasoning",
        "response:",
    ]
    
    if any(marker in lower for marker in reasoning_markers):
        return ""
    
    # Detect reasoning structure: numbered steps (1. 2. 3. etc)
    # Count lines that look like "1. " or "2. "
    step_pattern = re.compile(r'\d+\.\s+\*?\*?')
    if step_pattern.search(text):
        # If there are multiple numbered steps in reasoning format, reject
        steps = step_pattern.findall(text)
        if len(steps) > 1:
            return ""
    
    # Reject if it looks like meta-reasoning (starts with action words + the request)
    meta_starts = ["i need to", "the user wants", "i should", "we need to"]
    if any(lower.startswith(prefix) for prefix in meta_starts):
        return ""
    
    return line


def process(payload):
    prompt_bank = load_prompt_bank()
    prompt_row, elicitation_key = select_prompt(payload, prompt_bank)
    messages = build_messages(payload, prompt_row, elicitation_key)

    fallback_reason = ""
    try:
        reply = sanitize_reply(call_lmstudio(payload, messages))
        if not reply:
            raise ValueError("Unusable LM Studio reply")
        source = "lmstudio"
    except urllib.error.HTTPError as error:
        response_text = ""
        try:
            response_text = error.read().decode("utf-8", errors="replace").strip()
        except OSError:
            response_text = ""
        fallback_reason = f"http_error:{error.code} {response_text}".strip()
        reply = fallback_reply(payload, prompt_row)
        source = "fallback"
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
        fallback_reason = f"{type(error).__name__}: {error}"
        reply = fallback_reply(payload, prompt_row)
        source = "fallback"

    return {
        "ok": True,
        "source": source,
        "reply": reply,
        "prompt_id": prompt_row["prompt_id"],
        "strategy": prompt_row["strategy"],
        "phase": prompt_row["phase"],
        "prompt_text": prompt_row["text"],
        "fallback_reason": fallback_reason,
    }


def append_log_row(log_path, payload, result):
    if not log_path:
        return

    path = ROOT / log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    row = {
        "session_id": payload.get("session_id", "S01"),
        "group_id": payload.get("group_id", "G01"),
        "timestamp": now,
        "phase": result["phase"],
        "strategy": result["strategy"],
        "prompt_id": result["prompt_id"],
        "prompt_text": result["prompt_text"],
        "operator_trigger": payload.get("prompt_id", "manual"),
        "transition_reason": payload.get("transition_reason", "phase-matched prompt"),
        "response_window_start": now,
        "response_window_end": now,
        "notes": payload.get("notes", ""),
    }

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def print_robot_turn(result):
    print(f"[Robot:{result['source']}/{result['prompt_id']}] {result['reply']}")


def run_session(session_payload):
    print(f"# Session {session_payload.get('session_id', 'S01')} / group {session_payload.get('group_id', 'G01')}")
    for turn in session_payload.get("turns", []):
        speaker = turn.get("speaker", "Participant")
        text = turn.get("text", "")
        phase = turn.get("phase", session_payload.get("phase", "divergence"))

        print(f"[{speaker}] {text}")

        if speaker.lower() in {"robot", "pepper"}:
            continue

        request = dict(session_payload)
        request.update({
            "phase": phase,
            "last_user_utterance": text,
            "mode_combo": turn.get("mode_combo", session_payload.get("mode_combo", {})),
            "prompt_id": turn.get("prompt_id", session_payload.get("prompt_id")),
            "fallback_prompt": turn.get("fallback_prompt", session_payload.get("fallback_prompt")),
            "transition_reason": turn.get("transition_reason", session_payload.get("transition_reason", "phase-matched prompt")),
            "notes": turn.get("notes", session_payload.get("notes", "")),
        })
        result = process(request)
        print_robot_turn(result)
        append_log_row(session_payload.get("log_path"), request, result)


def console_receive():
    return input("Participant: ").strip()


def console_send(text):
    print(f"Robot: {text}")


# Pepper alternative, kept as a drop-in replacement for the console methods.
# def receive_from_pepper():
#     """Replace with NAOqi, REST, or a Pepper-specific message bus reader."""
#     return ...
#
# def send_to_pepper(text):
#     """Replace with Pepper TTS or tablet display output."""
#     ...


def interactive_loop(payload):
    current_phase = payload.get("phase", "divergence")
    print(f"--- Starting phase: {current_phase} ---")
    
    while True:
        line = console_receive()
        if not line:
            continue
        
        if line.upper() in {"CHANGE", "SWITCH", "NEXT PHASE"}:
            current_phase = "convergence" if current_phase == "divergence" else "divergence"
            print(f"--- Switched to phase: {current_phase} ---")
            continue
        
        if line.lower() in {"quit", "exit"}:
            break
        
        payload["phase"] = current_phase
        payload["last_user_utterance"] = line
        result = process(payload)
        print_robot_turn(result)
        append_log_row(payload.get("log_path"), payload, result)


def main():
    parser = argparse.ArgumentParser(description="Minimal LM Studio bridge for scripted elicitation prompts")
    parser.add_argument("--request", help="Path to a one-turn JSON request")
    parser.add_argument("--simulate", help="Path to a multi-turn session JSON file")
    parser.add_argument("--interactive", action="store_true", help="Read participant turns from the console")
    args = parser.parse_args()

    if not any([args.request, args.simulate, args.interactive]):
        parser.error("Choose --request, --simulate, or --interactive")

    if args.request:
        payload = load_json(pathlib.Path(args.request))
        result = process(payload)
        print(json.dumps(result, ensure_ascii=True))
        return

    if args.simulate:
        session_payload = load_json(pathlib.Path(args.simulate))
        run_session(session_payload)
        return

    if args.interactive:
        payload = {
            "server_url": "http://127.0.0.1:1234/v1/chat/completions",
            "model": "google/gemma-4-e4b",
            "session_id": "S01",
            "group_id": "G01",
            "theme": "How might TU Delft improve first-year transition and belonging?",
            "phase": "divergence",
            "log_path": "data/prompt_log.csv",
            "mode_combo": {
                "elicitation": "perspective_shift",
                "style": "passive",
                "initiative": "reactive"
            },
            "seed_ideas": [],
            "temperature": 0.35,
            "max_tokens": 60,
            "timeout_seconds": 8.0,
            "fallback_prompt": "What would this look like for a commuter student on their busiest week?"
        }
        interactive_loop(payload)


if __name__ == "__main__":
    main()