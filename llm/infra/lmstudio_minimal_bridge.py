#!/usr/bin/env python3
import argparse
import csv
import json
import os
import pathlib
import re
import time
import threading
import queue
import urllib.error
import urllib.request
import sys
import subprocess
try:
    import msvcrt
except Exception:
    msvcrt = None
try:
    from naoqi import ALProxy
except Exception:
    ALProxy = None


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPT_BANK = ROOT / "prompts" / "prompt_bank.csv"
COUNTERBALANCING = ROOT / "design" / "counterbalancing_elicitation.csv"
THEMES_FILE = ROOT / "design" / "themes.json"
DEFAULT_LOG_PATH = "logs/logs.csv"
PROACTIVE_SILENCE_THRESHOLD = 10  # seconds of silence to trigger proactive intervention
ASR_MEMORY_KEY = "WordRecognized"
LIVE_EXIT_WORDS = {"quit", "exit", "stop", "stop conversation"}
DEFAULT_NAOQI_SDK_ROOT = (
    r"C:\Users\Hrsem\Downloads"
    r"\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649"
    r"\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649"
)

DEFAULT_PEPPER_VOCABULARY = [
    "pepper",
    "robot",
    "hello",
    "continue",
    "next",
    "change",
    "idea",
    "budget",
]

MODE_REGISTRY = {
    "elicitation": ["perspective_shift", "generative", "elaboration_evidence"],
    "style": ["passive", "assertive"],
    "initiative": ["reactive", "proactive"],
}

ELICITATION_ALIASES = {
    "constraint_reframing": "generative",
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
    "generative": (
        "Use creative brainstorming techniques to generate new ideas."
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


def parse_phrase_list(value):
    if not value:
        return []
    parts = re.split(r"[,;\n]+", value)
    return [item.strip() for item in parts if item.strip()]


class PepperIO:
    def __init__(self, ip, port=9559, language="English", vocabulary=None):
        self.ip = ip
        self.port = int(port)
        self.language = language
        self.vocabulary = vocabulary or []
        self.tts = None
        self.asr = None
        self.memory = None
        self.subscriber_name = f"lmstudio_bridge_{int(time.time())}"
        self._last_word = ""
        self._last_word_time = 0.0

    def connect(self):
        if ALProxy is None:
            raise RuntimeError(
                "naoqi Python SDK not found. Install NAOqi Python bindings on the interpreter used to run this script."
            )

        self.tts = ALProxy("ALTextToSpeech", self.ip, self.port)
        self.memory = ALProxy("ALMemory", self.ip, self.port)
        self.asr = ALProxy("ALSpeechRecognition", self.ip, self.port)
        self.asr.setLanguage(self.language)

        if self.vocabulary:
            self.asr.pause(True)
            self.asr.setVocabulary(self.vocabulary, False)
            self.asr.pause(False)

        self.asr.subscribe(self.subscriber_name)

    def close(self):
        if self.asr:
            try:
                self.asr.unsubscribe(self.subscriber_name)
            except Exception:
                pass

    def say(self, text):
        if not text:
            return
        if self.tts:
            self.tts.say(text)

    def listen(self, timeout_seconds=12.0, min_confidence=0.45):
        if not self.memory:
            return ""

        deadline = time.time() + float(timeout_seconds)
        while time.time() < deadline:
            try:
                data = self.memory.getData(ASR_MEMORY_KEY)
            except Exception:
                data = None

            # ALMemory WordRecognized format: [word1, conf1, word2, conf2, ...]
            if isinstance(data, list) and len(data) >= 2:
                for idx in range(0, len(data) - 1, 2):
                    word = data[idx]
                    confidence = data[idx + 1]
                    if not isinstance(word, str):
                        continue
                    try:
                        confidence_value = float(confidence)
                    except Exception:
                        continue
                    cleaned = word.strip()
                    if cleaned and confidence_value >= float(min_confidence):
                        now = time.time()
                        # WordRecognized may keep the same value for a short time; ignore immediate duplicates.
                        if cleaned == self._last_word and (now - self._last_word_time) < 1.0:
                            continue
                        self._last_word = cleaned
                        self._last_word_time = now
                        return cleaned

            time.sleep(0.1)

        return ""


def send_to_pepper_via_py27(text, ip, port, script_path, python_cmd):
    if not text:
        return

    command = list(python_cmd) + [
        str(script_path),
        "--ip",
        str(ip),
        "--port",
        str(port),
        "--say",
        text,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=build_naoqi_subprocess_env(),
    )
    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        stdout_text = (completed.stdout or "").strip()
        details = stderr_text or stdout_text or "unknown error"
        raise RuntimeError(f"Python 2.7 Pepper TTS bridge failed: {details}")


def receive_from_pepper_via_py27(ip, port, language, vocabulary, timeout_seconds, min_confidence, script_path, python_cmd):
    command = list(python_cmd) + [
        str(script_path),
        "--ip",
        str(ip),
        "--port",
        str(port),
        "--language",
        str(language),
        "--timeout",
        str(timeout_seconds),
        "--min-confidence",
        str(min_confidence),
    ]
    if vocabulary:
        command.extend(["--vocabulary", ",".join(vocabulary)])

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=build_naoqi_subprocess_env(),
    )
    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        stdout_text = (completed.stdout or "").strip()
        details = stderr_text or stdout_text or "unknown error"
        raise RuntimeError(f"Python 2.7 Pepper ASR bridge failed: {details}")

    return (completed.stdout or "").strip()


def build_naoqi_subprocess_env():
    env = os.environ.copy()
    sdk_root = env.get("NAOQI_SDK_ROOT", DEFAULT_NAOQI_SDK_ROOT)
    sdk_lib = pathlib.Path(sdk_root) / "lib"
    sdk_bin = pathlib.Path(sdk_root) / "bin"

    if (sdk_lib / "naoqi.py").exists():
        existing_pythonpath = env.get("PYTHONPATH", "")
        pythonpath_parts = [str(sdk_lib)]
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join([str(sdk_bin), str(sdk_lib), existing_path])

    return env


def build_pepper_tts_sender(args):
    if ALProxy is None:
        legacy_python_cmd = args.pepper_legacy_python.strip().split()
        legacy_script = pathlib.Path(args.pepper_legacy_tts_script)
        if not legacy_script.is_absolute():
            legacy_script = (ROOT.parent / legacy_script).resolve()

        if not legacy_script.exists():
            raise RuntimeError(f"Legacy TTS script not found: {legacy_script}")

        print("naoqi SDK unavailable in Python 3; using Python 2.7 Pepper TTS bridge.")

        def sender(text):
            send_to_pepper_via_py27(
                text=text,
                ip=args.pepper_ip,
                port=args.pepper_port,
                script_path=legacy_script,
                python_cmd=legacy_python_cmd,
            )

        return sender, None

    pepper = PepperIO(
        ip=args.pepper_ip,
        port=args.pepper_port,
        language=args.pepper_language,
        vocabulary=parse_phrase_list(args.pepper_vocabulary),
    )
    pepper.connect()
    print(f"Connected to Pepper at {args.pepper_ip}:{args.pepper_port}")

    def sender(text):
        pepper.say(text)

    return sender, pepper


def build_pepper_receiver(args, pepper):
    if pepper is not None:
        def receiver():
            print("Listening via Pepper...")
            text = pepper.listen(
                timeout_seconds=args.asr_timeout,
                min_confidence=args.asr_min_confidence,
            )
            if text:
                print(f"Participant: {text}")
            return text

        return receiver

    legacy_python_cmd = args.pepper_legacy_python.strip().split()
    legacy_script = pathlib.Path(args.pepper_legacy_asr_script)
    if not legacy_script.is_absolute():
        legacy_script = (ROOT.parent / legacy_script).resolve()

    if not legacy_script.exists():
        raise RuntimeError(f"Legacy ASR script not found: {legacy_script}")

    vocabulary = parse_phrase_list(args.pepper_vocabulary)

    def receiver():
        print("Listening via Pepper...")
        text = receive_from_pepper_via_py27(
            ip=args.pepper_ip,
            port=args.pepper_port,
            language=args.pepper_language,
            vocabulary=vocabulary,
            timeout_seconds=args.asr_timeout,
            min_confidence=args.asr_min_confidence,
            script_path=legacy_script,
            python_cmd=legacy_python_cmd,
        )
        if text:
            print(f"Participant: {text}")
        return text

    return receiver


def format_history_window(history, window_turns):
    if not history:
        return "none"

    window = history[-window_turns:]
    lines = []
    for item in window:
        timestamp = item.get("timestamp", "")
        speaker = item.get("speaker", "Participant")
        text = item.get("text", "")
        lines.append(f"{timestamp} {speaker}: {text}".strip())
    return "\n".join(lines)


def load_json(path):
    # utf-8-sig also handles JSON files saved from PowerShell with BOM.
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_prompt_bank():
    with PROMPT_BANK.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_counterbalancing():
    with COUNTERBALANCING.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_themes():
    if not THEMES_FILE.exists():
        return {}
    try:
        with THEMES_FILE.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
            return data.get("themes", {})
    except Exception as e:
        print(f"Warning: Could not load themes: {e}")
        return {}


def get_mode_value(payload, key):
    selected = payload.get("mode_combo", {}).get(key, MODE_DEFAULTS[key])
    if key == "elicitation":
        selected = ELICITATION_ALIASES.get(selected, selected)
    if selected not in MODE_REGISTRY[key]:
        raise ValueError(f"Invalid mode for {key}: {selected}")
    return selected


def normalize_elicitation(value):
    return ELICITATION_ALIASES.get(value, value)


def get_strategy_sequence(group_id, theme_id, phase):
    rows = load_counterbalancing()
    for row in rows:
        if row.get("group_id") == group_id and row.get("theme_id") == theme_id and row.get("phase") == phase:
            return [
                normalize_elicitation(row.get("order_slot_1", "")),
                normalize_elicitation(row.get("order_slot_2", "")),
                normalize_elicitation(row.get("order_slot_3", "")),
            ]
    raise ValueError(f"No counterbalancing row for {group_id}/{theme_id}/{phase}")


def select_first_prompt_for_strategy(prompt_bank, strategy, phase):
    for row in prompt_bank:
        if normalize_elicitation(row.get("strategy", "")) == strategy and row.get("phase") == phase:
            return row
    return None


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
    history_window_turns = int(payload.get("history_window_turns", 10))
    history_text = format_history_window(payload.get("conversation_history", []), history_window_turns)
    pending_turns = payload.get("recent_participant_turns", [])
    pending_text = "\n".join(
        f"{item.get('timestamp', '')} {item.get('speaker', 'Participant')}: {item.get('text', '')}".strip()
        for item in pending_turns
    ) if pending_turns else "none"

    system_text = (
        f"{BASE_SETUP} "
        f"Elicitation mode guidance: {ELICITATION_SETUP[elicitation_key]} "
        f"Style guidance: {STYLE_SETUP[style_key]} "
        f"Initiative guidance: {INITIATIVE_SETUP[initiative_key]}"
    )

    user_text = (
        f"Theme: {payload.get('theme', '')}\n"
        f"Phase: {payload.get('phase', '')}\n"
        f"Recent conversation history:\n{history_text}\n"
        f"Latest uninterrupted participant turns:\n{pending_text}\n"
        f"Last participant utterance: {payload.get('last_user_utterance', '')}\n"
        f"Seed ideas: {seed_text}\n"
        f"Intervention to deliver: {prompt_row['text']}\n"
        "Respond naturally as if speaking in the conversation, not as meta-commentary. Keep your response brief and direct."
    )

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def build_messages_context_only(payload):
    style_key = get_mode_value(payload, "style")
    initiative_key = get_mode_value(payload, "initiative")
    seed_ideas = payload.get("seed_ideas", [])
    seed_text = "; ".join(seed_ideas) if seed_ideas else "none"
    history_window_turns = int(payload.get("history_window_turns", 10))
    history_text = format_history_window(payload.get("conversation_history", []), history_window_turns)
    pending_turns = payload.get("recent_participant_turns", [])
    pending_text = "\n".join(
        f"{item.get('timestamp', '')} {item.get('speaker', 'Participant')}: {item.get('text', '')}".strip()
        for item in pending_turns
    ) if pending_turns else "none"

    system_text = (
        f"{BASE_SETUP} "
        f"Style guidance: {STYLE_SETUP[style_key]} "
        f"Initiative guidance: {INITIATIVE_SETUP[initiative_key]} "
        "No predefined intervention prompt is active. Respond only based on conversation context."
    )

    user_text = (
        f"Theme: {payload.get('theme', '')}\n"
        f"Phase: {payload.get('phase', '')}\n"
        f"Recent conversation history:\n{history_text}\n"
        f"Latest uninterrupted participant turns:\n{pending_text}\n"
        f"Last participant utterance: {payload.get('last_user_utterance', '')}\n"
        f"Seed ideas: {seed_text}\n"
        "Respond naturally and briefly based only on the context above."
    )

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def call_lmstudio(payload, messages):
    body = {
        "model": payload.get("model", "phi-3.5-mini-3.8b-instruct"),
        "messages": messages,
        "temperature": payload.get("temperature", 0.35),
        "max_tokens": payload.get("max_tokens", 600),
        "thinking": False,
        "enable_thinking": False,
    }

    request = urllib.request.Request(
        payload.get("server_url", "http://127.0.0.1:1234/v1/chat/completions"),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    timeout = float(payload.get("timeout_seconds", 30.0))
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


def process_context_only(payload):
    messages = build_messages_context_only(payload)
    fallback_reason = ""
    fallback_text = payload.get("context_fallback") or "Could you expand on that a bit more?"

    try:
        reply = sanitize_reply(call_lmstudio(payload, messages))
        if not reply:
            raise ValueError("Unusable LM Studio reply")
        source = "lmstudio"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
        fallback_reason = f"{type(error).__name__}: {error}"
        reply = fallback_text
        source = "fallback"

    return {
        "ok": True,
        "source": source,
        "reply": reply,
        "prompt_id": "",
        "strategy": "context_only",
        "phase": payload.get("phase", "divergence"),
        "prompt_text": "",
        "fallback_reason": fallback_reason,
    }


def append_log_conversation_header(handle, session_id, group_id, conversation_id):
    handle.write(f"{session_id},{group_id},{conversation_id}\n")
    handle.write("\n")


def append_log_turn_block(log_path, payload, result, participant_turns, robot_timestamp):
    if not log_path:
        return

    path = ROOT / log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    session_id = payload.get("session_id", "S01")
    group_id = payload.get("group_id", "G01")
    conversation_id = payload.get("conversation_id", session_id)
    prompt_text = result["prompt_text"]
    robot_reply = result["reply"]
    turn_timestamp = payload.get("turn_timestamp")
    if not turn_timestamp and participant_turns:
        turn_timestamp = participant_turns[0].get("timestamp", "")
    if not turn_timestamp:
        turn_timestamp = robot_timestamp

    needs_header = (not path.exists()) or path.stat().st_size == 0 or payload.get("turn_index", 1) == 1

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if needs_header:
            if path.exists() and path.stat().st_size > 0:
                handle.write("\n")
            append_log_conversation_header(handle, session_id, group_id, conversation_id)

        writer.writerow([turn_timestamp, result["phase"], result["strategy"], result["prompt_id"], "", prompt_text])
        for participant_turn in participant_turns:
            writer.writerow([
                participant_turn.get("timestamp", turn_timestamp),
                participant_turn.get("speaker", "Participant"),
                participant_turn.get("text", ""),
            ])
        writer.writerow([robot_timestamp, robot_reply])
        handle.write("\n")


def print_robot_turn(result):
    print(f"[Robot:{result['source']}/{result['prompt_id']}] {result['reply']}")


def run_session(session_payload):
    print(f"# Session {session_payload.get('session_id', 'S01')} / group {session_payload.get('group_id', 'G01')}")
    history = list(session_payload.get("conversation_history", []))
    pending_participant_turns = []
    if "intervene_every_n_participant_turns" in session_payload:
        raw_value = session_payload.get("intervene_every_n_participant_turns")
        intervene_every = int(raw_value) if raw_value else None
    else:
        intervene_every = 1

    for turn_index, turn in enumerate(session_payload.get("turns", []), start=1):
        speaker = turn.get("speaker", "Participant")
        text = turn.get("text", "")
        phase = turn.get("phase", session_payload.get("phase", "divergence"))
        turn_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        print(f"[{speaker}] {text}")

        history.append({
            "speaker": speaker,
            "text": text,
            "timestamp": turn_timestamp,
        })

        if speaker.lower() in {"robot", "pepper"}:
            continue

        pending_participant_turns.append({
            "speaker": speaker,
            "text": text,
            "timestamp": turn_timestamp,
        })

        # Determine initiative-driven intervention behavior
        should_intervene = bool(turn.get("force_robot", False))
        # honor the existing intervene_every setting
        if intervene_every is not None:
            should_intervene = should_intervene or len(pending_participant_turns) >= intervene_every

        # Initiative mode from session payload (default reactive)
        initiative_mode = session_payload.get("mode_combo", {}).get("initiative", MODE_DEFAULTS["initiative"])

        if initiative_mode == "reactive":
            # Reactive: only intervene if the robot's name is called in the latest participant text
            last_text = pending_participant_turns[-1]["text"].lower() if pending_participant_turns else ""
            if "pepper" in last_text or "robot" in last_text:
                should_intervene = should_intervene or True
            else:
                # If not explicitly called, skip intervention unless forced
                if not should_intervene:
                    continue

        elif initiative_mode == "proactive":
            # Proactive: trigger if silence exceeded threshold since last participant turn
            # pending_participant_turns contains the buffered turns. We'll check the timestamp of last turn.
            try:
                last_ts = pending_participant_turns[-1]["timestamp"] if pending_participant_turns else None
                if last_ts:
                    last_struct = time.strptime(last_ts, "%Y-%m-%dT%H:%M:%S")
                    last_epoch = time.mktime(last_struct)
                    elapsed = time.time() - last_epoch
                    if elapsed >= PROACTIVE_SILENCE_THRESHOLD:
                        should_intervene = True
            except Exception:
                # If parsing fails, fall back to existing should_intervene
                pass

        # If still not flagged for intervention, continue
        if not should_intervene:
            continue

        participant_timestamp = pending_participant_turns[0]["timestamp"]
        recent_turn_lines = [f"{item['speaker']}: {item['text']}" for item in pending_participant_turns]
        participant_log_text = " || ".join(recent_turn_lines)

        request = dict(session_payload)
        request.update({
            "conversation_id": session_payload.get("conversation_id", session_payload.get("session_id", "S01")),
            "turn_index": turn_index,
            "turn_timestamp": turn_timestamp,
            "phase": phase,
            "last_user_utterance": pending_participant_turns[-1]["text"],
            "recent_participant_turns": list(pending_participant_turns),
            "conversation_history": history,
            "mode_combo": turn.get("mode_combo", session_payload.get("mode_combo", {})),
            "prompt_id": turn.get("prompt_id", session_payload.get("prompt_id")),
            "fallback_prompt": turn.get("fallback_prompt", session_payload.get("fallback_prompt")),
            "transition_reason": turn.get("transition_reason", session_payload.get("transition_reason", "phase-matched prompt")),
            "notes": turn.get("notes", session_payload.get("notes", "")),
        })

        use_context_only = bool(turn.get("use_context_only", False))
        result = process_context_only(request) if use_context_only else process(request)
        robot_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        history.append({
            "speaker": "Robot",
            "text": result["reply"],
            "timestamp": robot_timestamp,
        })

        print_robot_turn(result)
        append_log_turn_block(session_payload.get("log_path"), request, result, pending_participant_turns, robot_timestamp)
        pending_participant_turns = []


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


def run_live_dialog(base_payload, receive_fn, send_fn):
    print("--- Live dialog mode started ---")
    print("Say/type one participant turn at a time. Type quit/exit/stop to end.")

    conversation_history = base_payload.setdefault("conversation_history", [])
    turn_index = 0

    while True:
        participant_text = (receive_fn() or "").strip()
        if not participant_text:
            continue

        if participant_text.lower() in LIVE_EXIT_WORDS:
            print("--- Live dialog stopped ---")
            break

        participant_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        participant_turn = {
            "speaker": "Participant",
            "text": participant_text,
            "timestamp": participant_timestamp,
        }
        conversation_history.append(participant_turn)

        turn_index += 1
        request = dict(base_payload)
        request.update({
            "turn_index": turn_index,
            "turn_timestamp": participant_timestamp,
            "last_user_utterance": participant_text,
            "recent_participant_turns": [participant_turn],
            "conversation_history": conversation_history,
        })

        result = process_context_only(request)
        robot_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

        conversation_history.append({
            "speaker": "Robot",
            "text": result["reply"],
            "timestamp": robot_timestamp,
        })

        print_robot_turn(result)
        send_fn(result["reply"])
        append_log_turn_block(
            base_payload.get("log_path"),
            request,
            result,
            [participant_turn],
            robot_timestamp,
        )



def main():
    parser = argparse.ArgumentParser(description="Minimal LM Studio bridge for scripted elicitation prompts")
    parser.add_argument("--request", help="Path to a one-turn JSON request")
    parser.add_argument("--simulate", help="Path to a multi-turn session JSON file")
    parser.add_argument("--intervene", action="store_true", help="Manual intervention mode with counterbalancing schedule")
    parser.add_argument("--live", action="store_true", help="Live conversation mode (console or Pepper I/O)")
    parser.add_argument("--initiative", choices=["reactive", "proactive"], help="Default initiative mode for intervene (reactive|proactive)")
    parser.add_argument("--theme", default="Open brainstorming conversation", help="Theme text used in live mode")
    parser.add_argument("--pepper", action="store_true", help="Use Pepper NAOqi I/O in live mode")
    parser.add_argument("--pepper-ip", default="192.168.1.35", help="Pepper robot IP")
    parser.add_argument("--pepper-port", type=int, default=9559, help="Pepper NAOqi port")
    parser.add_argument("--pepper-language", default="English", help="Pepper ASR language")
    parser.add_argument("--pepper-vocabulary", help="Comma-separated vocabulary for Pepper ASR")
    parser.add_argument("--asr-timeout", type=float, default=12.0, help="Seconds to wait for one Pepper ASR result")
    parser.add_argument("--asr-min-confidence", type=float, default=0.45, help="Minimum confidence for Pepper ASR result")
    parser.add_argument("--pepper-legacy-python", default="py -2.7-64", help="Python launcher command for Python 2.7 Pepper helper")
    parser.add_argument("--pepper-legacy-tts-script", default=str(ROOT.parent / "pepper" / "tts.py"), help="Path to Python 2.7 Pepper TTS helper script")
    parser.add_argument("--pepper-legacy-asr-script", default=str(ROOT.parent / "pepper" / "asr.py"), help="Path to Python 2.7 Pepper ASR helper script")
    args = parser.parse_args()

    if not any([args.request, args.simulate, args.intervene, args.live]):
        parser.error("Choose --request, --simulate, --intervene, or --live")

    if args.request:
        payload = load_json(pathlib.Path(args.request))
        result = process(payload)
        print(json.dumps(result, ensure_ascii=True))
        return

    if args.simulate:
        session_payload = load_json(pathlib.Path(args.simulate))
        run_session(session_payload)
        return

    if args.live:
        payload = {
            "server_url": "http://127.0.0.1:1234/v1/chat/completions",
            "model": "phi-3.5-mini-3.8b-instruct",
            "session_id": f"LIVE_{int(time.time())}",
            "group_id": "G_LIVE",
            "conversation_id": f"LIVE_{int(time.time())}",
            "theme": args.theme,
            "phase": "divergence",
            "log_path": DEFAULT_LOG_PATH,
            "mode_combo": {
                "elicitation": "perspective_shift",
                "style": "passive",
                "initiative": "reactive",
            },
            "seed_ideas": [],
            "conversation_history": [],
            "history_window_turns": 12,
            "temperature": 0.35,
            "max_tokens": 600,
            "timeout_seconds": 30.0,
            "context_fallback": "Could you tell me a little more?",
        }

        if not args.pepper:
            run_live_dialog(payload, console_receive, console_send)
            return

        send_to_pepper, pepper = build_pepper_tts_sender(args)
        receive_from_pepper = build_pepper_receiver(args, pepper)
        print("Pepper will listen for participant speech and speak each LLM reply.")

        try:
            run_live_dialog(payload, receive_from_pepper, send_to_pepper)
        finally:
            if pepper:
                pepper.close()
        return

    if args.intervene:
        group_id = input("Group ID (e.g. G01): ").strip().upper()
        theme_id = input("Theme ID (e.g. T1 or T2): ").strip().upper()

        themes = load_themes()
        theme_text = ""
        if theme_id in themes:
            theme_text = themes[theme_id].get("description", "")
            print(f"[Using predefined theme {theme_id}]")
        else:
            theme_text = input("Theme text: ").strip()
            if not theme_text:
                print("Error: Theme not recognized and no text provided.")
                return

        chosen_initiative = args.initiative or "reactive"

        payload = {
            "server_url": "http://127.0.0.1:1234/v1/chat/completions",
    "model": "phi-3.5-mini-3.8b-instruct",
            "session_id": f"S_{group_id}_{theme_id}",
            "group_id": group_id,
            "conversation_id": f"{group_id}_{theme_id}_{int(time.time())}",
            "theme": theme_text,
            "theme_id": theme_id,
            "phase": "divergence",
            "log_path": DEFAULT_LOG_PATH,
            "mode_combo": {
                "elicitation": "perspective_shift",
                "style": "passive",
                "initiative": chosen_initiative,
            },
            "seed_ideas": [],
            "conversation_history": [],
            "history_window_turns": 12,
            "temperature": 0.35,
            "max_tokens": 600,
            "timeout_seconds": 30.0,
            "fallback_prompt": "Could you expand on that from a different angle?",
            "context_fallback": "Could you expand on that a bit more?",
        }

        prompt_bank = load_prompt_bank()
        strategy_sequences = {
            "divergence": get_strategy_sequence(group_id, theme_id, "divergence"),
            "convergence": get_strategy_sequence(group_id, theme_id, "convergence"),
        }
        used_strategies = {"divergence": [], "convergence": []}
        phase_reply_count = {"divergence": 0, "convergence": 0}
        pending_participant_turns = []
        turn_index = 0
        current_phase = "divergence"

        print("--- Intervene mode started ---")
        print("Commands: CHANGE (phase switch), ROBOT (robot intervention), PROACTIVE/REACTIVE (switch initiative), exit (stop)")

        say_from_intervene = None
        pepper_tts_client = None
        if args.pepper:
            say_from_intervene, pepper_tts_client = build_pepper_tts_sender(args)
            print("Pepper TTS enabled for intervene mode.")

        # Event queue and monitor thread for proactive silence detection
        event_queue = queue.Queue()
        input_queue = queue.Queue()
        stop_event = threading.Event()
        last_triggered_last_epoch = 0
        input_lock = threading.Lock()
        current_input_line = [""]  # list so it's mutable in nested scope

        def monitor_silence():
            nonlocal last_triggered_last_epoch
            while not stop_event.is_set():
                try:
                    initiative = payload.get("mode_combo", {}).get("initiative", "reactive")
                    if initiative == "proactive" and pending_participant_turns:
                        last_ts = pending_participant_turns[-1].get("timestamp")
                        if last_ts:
                            try:
                                last_struct = time.strptime(last_ts, "%Y-%m-%dT%H:%M:%S")
                                last_epoch = time.mktime(last_struct)
                                elapsed = time.time() - last_epoch
                                if elapsed >= PROACTIVE_SILENCE_THRESHOLD and last_epoch != last_triggered_last_epoch:
                                    event_queue.put("AUTO_ROBOT")
                                    last_triggered_last_epoch = last_epoch
                            except Exception:
                                pass
                except Exception:
                    pass
                time.sleep(1)

        monitor_thread = threading.Thread(target=monitor_silence, daemon=True)
        monitor_thread.start()

        # Background input reader so main loop isn't blocked on input(); lets AUTO_ROBOT be handled immediately
        def input_reader():
            # If msvcrt is available (Windows), use character-based input to avoid needing to press Enter after prints.
            if msvcrt:
                sys.stdout.write("Participant: ")
                sys.stdout.flush()
                line = ""
                while not stop_event.is_set():
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch == "\r":
                            # Enter pressed
                            print("")
                            with input_lock:
                                current_input_line[0] = ""
                            input_queue.put(line)
                            line = ""
                            sys.stdout.write("Participant: ")
                            sys.stdout.flush()
                        elif ch == "\x08":
                            # Backspace
                            if len(line) > 0:
                                line = line[:-1]
                                with input_lock:
                                    current_input_line[0] = line
                                sys.stdout.write('\b \b')
                                sys.stdout.flush()
                        elif ch in ('\x00', '\xe0'):
                            # special key, consume next
                            msvcrt.getwch()
                        else:
                            line += ch
                            with input_lock:
                                current_input_line[0] = line
                            sys.stdout.write(ch)
                            sys.stdout.flush()
                    else:
                        time.sleep(0.05)
            else:
                # Fallback for non-Windows: blocking input
                while not stop_event.is_set():
                    try:
                        line = input("Participant: ").strip()
                    except EOFError:
                        stop_event.set()
                        break
                    input_queue.put(line)

        input_thread = threading.Thread(target=input_reader, daemon=True)
        input_thread.start()

        # Helper to perform the robot intervention; extracted to reuse for manual, auto, and reactive triggers
        def trigger_robot():
            nonlocal turn_index, pending_participant_turns
            if not pending_participant_turns:
                print("Robot: No participant turns buffered yet.")
                return

            nonlocal_vars = None
            turn_index += 1
            participant_timestamp = pending_participant_turns[0]["timestamp"]
            recent_turn_lines = [f"{item['speaker']}: {item['text']}" for item in pending_participant_turns]
            participant_log_text = " || ".join(recent_turn_lines)

            payload["phase"] = current_phase
            payload["turn_index"] = turn_index
            payload["turn_timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            payload["last_user_utterance"] = pending_participant_turns[-1]["text"]
            payload["recent_participant_turns"] = list(pending_participant_turns)
            payload["conversation_history"] = payload.get("conversation_history", [])

            next_reply_index = phase_reply_count[current_phase] + 1
            schedule_slot = (next_reply_index % 4 == 0)
            result = None

            if schedule_slot:
                planned = strategy_sequences[current_phase]
                next_strategy = None
                for strategy in planned:
                    if strategy and strategy not in used_strategies[current_phase]:
                        next_strategy = strategy
                        break

                if next_strategy:
                    prompt_row = select_first_prompt_for_strategy(prompt_bank, next_strategy, current_phase)
                    if prompt_row:
                        payload["mode_combo"] = dict(payload.get("mode_combo", {}))
                        payload["mode_combo"]["elicitation"] = next_strategy
                        payload["prompt_id"] = prompt_row["prompt_id"]
                        result = process(payload)
                        used_strategies[current_phase].append(next_strategy)

            if result is None:
                payload["prompt_id"] = ""
                result = process_context_only(payload)

            robot_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            payload.setdefault("conversation_history", []).append(
                {"speaker": "Robot", "text": result["reply"], "timestamp": robot_timestamp}
            )
            print_robot_turn(result)
            if say_from_intervene:
                try:
                    say_from_intervene(result["reply"])
                except Exception as error:
                    print(f"Warning: Pepper TTS failed: {error}")
            append_log_turn_block(payload.get("log_path"), payload, result, list(pending_participant_turns), robot_timestamp)
            phase_reply_count[current_phase] += 1
            pending_participant_turns = []

            # After robot output, redraw the prompt and restore current input buffer (Windows msvcrt path)
            try:
                with input_lock:
                    buf = current_input_line[0]
                if msvcrt:
                    # Clear current line and rewrite prompt + buffer
                    sys.stdout.write('\r')
                    sys.stdout.write(' ' * (len('Participant: ') + len(buf) + 2))
                    sys.stdout.write('\r')
                    sys.stdout.write('Participant: ' + buf)
                    sys.stdout.flush()
                else:
                    # On non-Windows, just print the prompt so user sees it
                    sys.stdout.write('\nParticipant: ')
                    sys.stdout.flush()
            except Exception:
                pass

        while True:
            # Handle any queued auto-trigger events first
            try:
                ev = event_queue.get_nowait()
            except queue.Empty:
                ev = None

            if ev == "AUTO_ROBOT":
                # Auto-trigger from proactive monitor
                trigger_robot()
                # After robot prints, redraw prompt and current input buffer so user can continue typing without pressing Enter
                try:
                    with input_lock:
                        buf = current_input_line[0]
                    # Carriage return and clear line
                    sys.stdout.write('\r')
                    sys.stdout.write(' ' * (len('Participant: ') + len(buf) + 2))
                    sys.stdout.write('\r')
                    sys.stdout.write('Participant: ' + buf)
                    sys.stdout.flush()
                except Exception:
                    pass
                continue
            else:
                # Non-blocking check for user input
                try:
                    line = input_queue.get_nowait()
                except queue.Empty:
                    line = None

                if line is None:
                    # Nothing to do right now, yield briefly
                    time.sleep(0.1)
                    continue
                upper = line.upper()

            if line and line.lower() in {"quit", "exit"}:
                stop_event.set()
                # allow background threads to exit
                monitor_thread.join(timeout=1)
                # input_thread is daemon; it will exit on program termination
                if pepper_tts_client:
                    pepper_tts_client.close()
                break

            if upper in {"CHANGE", "SWITCH", "NEXT PHASE"}:
                current_phase = "convergence" if current_phase == "divergence" else "divergence"
                print(f"--- Switched to phase: {current_phase} ---")
                continue

            # Allow switching initiative during the session
            if upper in {"PROACTIVE", "REACTIVE"}:
                new_mode = upper.lower()
                payload.setdefault("mode_combo", {})["initiative"] = new_mode
                print(f"--- Initiative switched to: {new_mode} ---")
                continue

            if upper == "ROBOT":
                trigger_robot()
                continue

            participant_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            speaker = "Participant"
            text = line
            if ":" in line:
                left, right = line.split(":", 1)
                if left.strip() and right.strip():
                    speaker = left.strip()
                    text = right.strip()

            payload.setdefault("conversation_history", []).append(
                {"speaker": speaker, "text": text, "timestamp": participant_timestamp}
            )
            pending_participant_turns.append({"speaker": speaker, "text": text, "timestamp": participant_timestamp})

            # Reactive immediate trigger: if initiative is reactive and the participant called the robot's name, intervene now
            try:
                initiative_now = payload.get("mode_combo", {}).get("initiative", MODE_DEFAULTS["initiative"])
                if initiative_now == "reactive":
                    last_text = text.lower()
                    if "pepper" in last_text or "robot" in last_text:
                        trigger_robot()
                        continue
            except Exception:
                pass


if __name__ == "__main__":
    main()
