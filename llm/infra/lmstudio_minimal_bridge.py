#!/usr/bin/env python3
import argparse
import csv
import io
import json
import os
import pathlib
import re
import time
import threading
import queue
import urllib.error
import urllib.request
import wave
import sys
import subprocess
import socket
try:
    import httpx
except Exception:
    httpx = None
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
DEFAULT_TRANSCRIPT_LOG_PATH = "logs/transcript.csv"
DEFAULT_DEEPGRAM_API_KEY = ""
DEEPGRAM_API_KEY_ENV_VAR = "DEEPGRAM_API_KEY"
DEFAULT_PEPPER_LEGACY_PYTHON = r"C:\Python27\python.exe"
PROACTIVE_SILENCE_THRESHOLD = 7  # seconds of silence to trigger proactive intervention
ASR_MEMORY_KEY = "WordRecognized"
LIVE_EXIT_WORDS = {"quit", "exit", "stop", "stop conversation"}
DEFAULT_NAOQI_SDK_ROOT = (
    r"C:\Users\Hrsem\Downloads"
    r"\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649"
    r"\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649"
)
DEFAULT_AUDIO_INPUT_MODE = "focusrite"
DEFAULT_AUDIO_CAPTURE_MODE = "continuous"
FOCUSRITE_DEVICE_PATTERNS = ("focusrite", "scarlett")
LAPTOP_DEVICE_PATTERNS = (
    "microphone array",
    "internal microphone",
    "realtek",
    "intel smart sound",
    "laptop",
)
DEFAULT_FOCUSRITE_PARTICIPANTS = (
    (1, "Participant 1"),
    (2, "Participant 2"),
)
DEFAULT_TRIGGER_WORDS = ("pepper", "paper")
TRIGGER_WORD_ALIASES = {
    "pepper": ("paper",),
}
DEFAULT_PROACTIVE_STRUGGLE_CUES = (
    "i don't know",
    "we're stuck",
    "no ideas",
    "this is hard",
    "nothing works",
    "we can't think of anything",
    "we are stuck",
    "we need help",
)
TRANSCRIPT_LOG_COLUMNS = [
    "session_id",
    "group_id",
    "conversation_id",
    "sequence_index",
    "robot_turn_index",
    "event_type",
    "timestamp",
    "start_timestamp",
    "end_timestamp",
    "speaker",
    "text",
    "phase",
    "strategy",
    "prompt_id",
    "source",
    "audio_input_mode",
    "audio_device",
    "audio_source",
    "audio_channel",
    "audio_rms",
    "triggered_robot",
    "trigger_words",
    "fallback_reason",
    "elicitation_engagement_score",
    "creative_confidence_score",
    "evaluation_moment",
    "previous_elicitation_prompt_id",
    "previous_elicitation_strategy",
    "previous_elicitation_phase",
]

# In-memory recent conversation buffer used for lightweight novelty detection
GLOBAL_CONVERSATION_HISTORY = []


DEFAULT_PEPPER_VOCABULARY = [
    "pepper",
    "paper",
    "robot",
    "hello",
    "continue",
    "next",
    "change",
    "idea",
    "budget",
]

CONTENT_TYPE_BY_EXT = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/m4a",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
}

MODE_REGISTRY = {
    "elicitation": ["perspective_shift", "generative", "elaboration_evidence"],
    "style": ["passive", "assertive", "supportive"],
    "initiative": ["reactive", "proactive"],
    "role": ["facilitator", "solutionist"],
}

ELICITATION_ALIASES = {
    "constraint_reframing": "generative",
}

MODE_DEFAULTS = {
    "elicitation": "perspective_shift",
    "style": "assertive",
    "initiative": "reactive",
    "role": "facilitator",
}

STYLE_SETUP = {
    "passive": "Use a gentle, low-pressure, non-directive tone.",
    "assertive": "Use a concise and direct facilitation tone while staying neutral. Be direct about ideas and suggesttions, be pushy and intervene more frequently to keep momentum. Use a more commanding tone to encourage participants to move forward and consider new directions. Don't be afraid to challenge ideas or suggest alternatives when the discussion stalls. Use words like 'You need to think about..' or 'Consider this...' to nudge participants towards new ideas.",
    "supportive": "Use a warm, encouraging, and empathetic tone. Focus on building rapport and making participants feel comfortable sharing their ideas. Use positive reinforcement and affirmations to validate participants' contributions. Be patient and allow for more time when participants are struggling, offering gentle prompts or reframing to help them find their way without pressure. Use phrases like 'That's a great point, and it makes me think of...' or 'I see where you're coming from, and it could also be interesting to consider...' to build on participants' ideas in a supportive way.",
}

VOCAL_DELIVERY = {
    "passive": {
        "speed": 88,           # NAOqi speech speed percentage; 100 is default
        "volume": 0.62,        # Softer volume for low-pressure delivery
        "pitch": 0.96,         # Best effort; ignored by NAOqi versions that do not support it
    },
    "assertive": {
        "speed": 120,          # Slightly quicker while still intelligible
        "volume": 0.78,        # Clear but not harsh
        "pitch": 0.98,
    },
    "supportive": {
        "speed": 80,           # Slower, more soothing delivery
        "volume": 0.55,        # Gentle default volume
        "pitch": 0.97,
    },
}

INITIATIVE_SETUP = {
    "reactive": "Intervene only after a pause or a direct prompt from participants.",
    "proactive": "Intervene when momentum drops or when phase goals are drifting.",
}

ROLE_SETUP = {
    "facilitator": "Act as a neutral facilitator guiding group discussions and brainstorming.",
    "solutionist": "Act as an active solutionist providing direct solutions and ideas instead of facilitating discussions.",
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
    "You are Pepper in a two-person brainstorming session. "
    "Respond naturally and conversationally, as if speaking aloud. "
    "Stay grounded in the latest participant turns and the recent history. "
    "Give one fluent spoken response that moves the discussion forward. "
    "Do not write dialogue labels, notes, markdown, analysis, explanations of your behavior, or multiple possible replies. "
    "If participants are joking, testing, or using profanity, stay calm and redirect briefly without scolding."
)
REPLY_CONTRACT = (
    "Output only Pepper's spoken words. "
    "Prioritize the latest participant turns over older history; do not invent a new topic. "
    "No speaker labels such as Pepper: or Robot:. "
    "No notes, markdown, separators, lists, or meta-commentary. "
    "Maximum 35 words."
)
DEFAULT_REPLY_MAX_WORDS = 35
DEFAULT_LM_STOP_SEQUENCES = [
    "\nParticipant",
    "\nParticipant 1",
    "\nParticipant 2",
    "\nRobot:",
    "\nPepper:",
    "\n---",
    "\nNote:",
    "\n**Note",
]


def parse_phrase_list(value):
    if not value:
        return []
    parts = re.split(r"[,;\n]+", value)
    return [item.strip() for item in parts if item.strip()]


def infer_audio_content_type(path):
    suffix = pathlib.Path(path).suffix.lower()
    return CONTENT_TYPE_BY_EXT.get(suffix, "application/octet-stream")


def parse_deepgram_transcript(response):
    # Deepgram returns transcript data under results.channels[0].alternatives[0].transcript
    if not isinstance(response, dict):
        return ""
    results = response.get("results", {})
    channels = results.get("channels") or []
    if channels:
        alternatives = channels[0].get("alternatives") or []
        if alternatives:
            return (alternatives[0].get("transcript") or "").strip()
    return (response.get("transcript") or "").strip()


def deepgram_transcribe_bytes(audio_data, content_type, api_key, endpoint="https://api.eu.deepgram.com/v1/listen", timeout=60.0):
    endpoint = endpoint.rstrip("/")
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": content_type,
    }

    if httpx is not None:
        response = httpx.post(endpoint, headers=headers, content=audio_data, timeout=timeout)
        response.raise_for_status()
        return parse_deepgram_transcript(response.json())

    request = urllib.request.Request(endpoint, data=audio_data, method="POST")
    for key, value in headers.items():
        request.add_header(key, value)

    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_deepgram_transcript(payload)


def deepgram_transcribe_file(audio_path, api_key, endpoint="https://api.eu.deepgram.com/v1/listen", timeout=60.0):
    audio_path = pathlib.Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    with audio_path.open("rb") as handle:
        audio_data = handle.read()

    return deepgram_transcribe_bytes(
        audio_data=audio_data,
        content_type=infer_audio_content_type(audio_path),
        api_key=api_key,
        endpoint=endpoint,
        timeout=timeout,
    )


def timestamp_from_epoch(epoch_seconds=None):
    if epoch_seconds is None:
        epoch_seconds = time.time()
    whole_seconds = int(epoch_seconds)
    milliseconds = int(round((float(epoch_seconds) - whole_seconds) * 1000))
    if milliseconds >= 1000:
        whole_seconds += 1
        milliseconds = 0
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(whole_seconds)) + f".{milliseconds:03d}"


def epoch_from_timestamp(timestamp):
    if not timestamp:
        return None
    base_timestamp = str(timestamp).split(".", 1)[0]
    parsed = time.strptime(base_timestamp, "%Y-%m-%dT%H:%M:%S")
    return time.mktime(parsed)


def resolve_log_path(log_path):
    if not log_path:
        return None
    path = pathlib.Path(log_path)
    if path.is_absolute():
        return path
    return ROOT / path


def load_local_env_file(env_path=None):
    path = pathlib.Path(env_path) if env_path else ROOT.parent / ".env"
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_env_secret(name, default=""):
    return os.environ.get(name) or default


def load_sounddevice():
    try:
        import sounddevice as sd
    except Exception as error:
        raise RuntimeError(
            "sounddevice is required for live microphone capture. Install it with `pip install sounddevice`."
        ) from error
    return sd


def parse_audio_device_identifier(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def get_default_input_device_index(sd):
    try:
        default_device = sd.default.device
    except Exception:
        return None

    if isinstance(default_device, (list, tuple)):
        default_device = default_device[0] if default_device else None

    try:
        default_device = int(default_device)
    except Exception:
        return None

    return default_device if default_device >= 0 else None


def format_input_device_list(sd):
    default_input = get_default_input_device_index(sd)
    lines = []
    for index, device in enumerate(sd.query_devices()):
        max_inputs = int(device.get("max_input_channels", 0) or 0)
        if max_inputs <= 0:
            continue
        name = device.get("name", "Unknown input")
        samplerate = device.get("default_samplerate", "unknown")
        marker = " [default input]" if index == default_input else ""
        lines.append(f"{index}: {name} ({max_inputs} input channel(s), default {samplerate} Hz){marker}")
    return lines or ["No input devices found."]


def print_audio_devices():
    sd = load_sounddevice()
    print("Available input devices:")
    for line in format_input_device_list(sd):
        print(f"  {line}")


def resolve_input_device(sd, preferred_device=None, patterns=(), min_input_channels=1, mode_name="audio", allow_default=False):
    devices = sd.query_devices()
    preferred = parse_audio_device_identifier(preferred_device)

    def usable_device(index):
        try:
            device = devices[int(index)]
        except Exception:
            return None
        max_inputs = int(device.get("max_input_channels", 0) or 0)
        return device if max_inputs >= int(min_input_channels) else None

    if isinstance(preferred, int):
        device = usable_device(preferred)
        if device is None:
            raise RuntimeError(
                f"Audio device {preferred} is not available or does not have "
                f"{min_input_channels} input channel(s)."
            )
        return preferred, device

    if isinstance(preferred, str):
        needle = preferred.lower()
        for index, device in enumerate(devices):
            name = str(device.get("name", ""))
            if needle in name.lower() and usable_device(index) is not None:
                return index, device

        available = "\n".join(f"  {line}" for line in format_input_device_list(sd))
        raise RuntimeError(
            f"Could not find requested {mode_name} input device matching {preferred!r}.\n"
            f"Available input devices:\n{available}"
        )

    for pattern in patterns:
        needle = pattern.lower()
        for index, device in enumerate(devices):
            name = str(device.get("name", ""))
            if needle in name.lower() and usable_device(index) is not None:
                return index, device

    if allow_default:
        default_index = get_default_input_device_index(sd)
        if default_index is not None:
            device = usable_device(default_index)
            if device is not None:
                return default_index, device

    available = "\n".join(f"  {line}" for line in format_input_device_list(sd))
    raise RuntimeError(
        f"Could not find a {mode_name} input device with {min_input_channels} input channel(s).\n"
        f"Available input devices:\n{available}\n"
        "Use --list-audio-devices to inspect names, then pass --audio-device with an index or name."
    )


def select_samplerate(device_info=None, requested_samplerate=None):
    if requested_samplerate:
        return int(requested_samplerate)
    if device_info:
        try:
            return int(float(device_info.get("default_samplerate") or 48000))
        except Exception:
            pass
    return 48000


def encode_wav_bytes(recording, samplerate, channels):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(int(channels))
        wf.setsampwidth(2)
        wf.setframerate(int(samplerate))
        wf.writeframes(recording.tobytes())
    return buffer.getvalue()


def record_audio_array(duration=6.0, samplerate=None, channels=1, device=None, device_info=None):
    sd = load_sounddevice()
    if device_info is None and device is not None:
        device_info = sd.query_devices(device)

    selected_samplerate = select_samplerate(device_info, samplerate)
    device_label = ""
    if device_info:
        device_label = f" on {device_info.get('name', 'selected device')}"

    print(
        f"Recording {channels} channel(s){device_label} "
        f"for up to {duration:.1f} seconds at {selected_samplerate} Hz..."
    )
    recording = sd.rec(
        int(duration * selected_samplerate),
        samplerate=selected_samplerate,
        channels=int(channels),
        dtype="int16",
        device=device,
    )
    sd.wait()
    return recording, selected_samplerate


def record_audio_from_mic(duration=6.0, samplerate=None, channels=1, device=None, device_info=None):
    recording, selected_samplerate = record_audio_array(
        duration=duration,
        samplerate=samplerate,
        channels=channels,
        device=device,
        device_info=device_info,
    )
    return encode_wav_bytes(recording, selected_samplerate, channels), selected_samplerate, channels


def extract_audio_channel(recording, channel_number):
    if int(channel_number) < 1:
        raise ValueError("Audio channel numbers are 1-based; use 1 for the first input.")

    shape = getattr(recording, "shape", ())
    if len(shape) == 1:
        channel_count = 1
    else:
        channel_count = int(shape[1])

    if int(channel_number) > channel_count:
        raise ValueError(f"Recorded audio only has {channel_count} channel(s); channel {channel_number} is unavailable.")

    if len(shape) == 1:
        return recording.reshape((-1, 1))

    channel_index = int(channel_number) - 1
    return recording[:, channel_index:channel_index + 1]


def calculate_audio_rms(recording):
    if getattr(recording, "size", 0) == 0:
        return 0.0
    samples = recording.reshape(-1).astype("float32")
    return float((samples * samples).mean() ** 0.5)


def parse_speaker_prefixed_text(text, default_speaker="Participant"):
    if ":" not in text:
        return default_speaker, text
    left, right = text.split(":", 1)
    if left.strip() and right.strip():
        return left.strip(), right.strip()
    return default_speaker, text


def make_participant_turn(speaker, text, timestamp=None, **metadata):
    turn = {
        "speaker": speaker or "Participant",
        "text": (text or "").strip(),
        "timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    for key, value in metadata.items():
        if value is not None:
            turn[key] = value
    return turn


def coerce_participant_turns(received, timestamp=None):
    if received is None:
        return []

    if isinstance(received, str):
        text = received.strip()
        if not text:
            return []
        speaker, clean_text = parse_speaker_prefixed_text(text)
        return [make_participant_turn(speaker, clean_text, timestamp=timestamp)]

    if isinstance(received, dict):
        text = str(received.get("text", "")).strip()
        if not text:
            return []
        return [make_participant_turn(
            received.get("speaker", "Participant"),
            text,
            timestamp=received.get("timestamp") or timestamp,
            audio_source=received.get("audio_source"),
            audio_channel=received.get("audio_channel"),
            audio_rms=received.get("audio_rms"),
        )]

    turns = []
    if isinstance(received, (list, tuple)):
        for item in received:
            turns.extend(coerce_participant_turns(item, timestamp=timestamp))
    return turns


def format_participant_turns_text(turns):
    pieces = []
    for turn in turns:
        speaker = turn.get("speaker", "Participant")
        text = turn.get("text", "")
        pieces.append(f"{speaker}: {text}" if speaker else text)
    return " || ".join(piece for piece in pieces if piece.strip())


def parse_trigger_words(value):
    words = parse_phrase_list(value) if isinstance(value, str) else list(value or [])
    cleaned = []
    for word in words:
        text = str(word).strip().lower()
        if text:
            cleaned.append(text)

    expanded = []
    seen = set()
    for word in cleaned or list(DEFAULT_TRIGGER_WORDS):
        for candidate in (word, *TRIGGER_WORD_ALIASES.get(word, ())):
            if candidate and candidate not in seen:
                expanded.append(candidate)
                seen.add(candidate)
    return expanded


def text_contains_trigger_word(text, trigger_words=DEFAULT_TRIGGER_WORDS):
    lower_text = (text or "").lower()
    for word in parse_trigger_words(trigger_words):
        w = word.lower()
        # Treat common mis-hearings and stems of 'pepper' as triggers (paper, papa, peper, paperwork...)
        if w == "pepper":
            alt = r"(?:pepper|paper|papa|peper|piper)"
            pattern = r"\b" + alt + r"\w*\b"
        else:
            pattern = r"(?<![a-z0-9_])" + re.escape(w) + r"(?![a-z0-9_])"
        if re.search(pattern, lower_text):
            return True
    return False


def text_contains_proactive_cue(text, cues=DEFAULT_PROACTIVE_STRUGGLE_CUES):
    lower_text = (text or "").lower()
    for cue in cues:
        if cue and cue.lower() in lower_text:
            return True
    return False


def should_trigger_proactive_robot(turn_text, trigger_words=DEFAULT_TRIGGER_WORDS, struggle_cues=DEFAULT_PROACTIVE_STRUGGLE_CUES):
    decision = evaluate_proactive_trigger(
        turn_text,
        trigger_words=trigger_words,
        struggle_cues=struggle_cues,
    )
    return bool(decision.get("triggered"))


# Novelty / lack-of-novelty detection
DEFAULT_NOVELTY_RECENT_TURNS = 4
DEFAULT_NOVELTY_PREV_TURNS = 8
DEFAULT_NOVELTY_MIN_NEW = 6
PROACTIVE_TRIGGER_SCORE_THRESHOLD = 2.5
PROACTIVE_TRIGGER_COOLDOWN_SECONDS = 20.0
ROBOT_IDLE_REPLY_THRESHOLD_SECONDS = 180.0
PROACTIVE_TRIGGER_WEIGHTS = {
    "trigger_word": 3.0,
    "struggle_cue": 2.5,
    "low_novelty": 2.5,
}


def _tokenize_text(text):
    tokens = re.findall(r"\b\w+\b", (text or "").lower())
    stopwords = {
        "the","and","a","an","to","of","in","on","for","is","are","it","that","this","we","you","i","they","he","she","be","or","as","with","not","but"
    }
    return [t for t in tokens if t and t not in stopwords and len(t) > 2]


def lacks_novelty(conversation_events, recent_turns=DEFAULT_NOVELTY_RECENT_TURNS, prev_turns=DEFAULT_NOVELTY_PREV_TURNS, min_new_keywords=DEFAULT_NOVELTY_MIN_NEW):
    """Return True when recent participant turns introduce fewer than `min_new_keywords`
    new meaningful tokens compared to the previous window.

    conversation_events: list of event dicts with at least a 'speaker' and 'text' key.
    """
    if not conversation_events or len(conversation_events) < 2:
        return False

    participant_turns = [e for e in conversation_events if str(e.get("speaker", "")).lower().startswith("participant")]
    if len(participant_turns) < 2:
        return False

    recent = participant_turns[-int(recent_turns):]
    if len(participant_turns) >= (int(recent_turns) + int(prev_turns)):
        prev = participant_turns[-(int(recent_turns) + int(prev_turns)):-int(recent_turns)]
    else:
        prev = participant_turns[:-int(recent_turns)]

    if not prev:
        return False

    prev_tokens = set()
    for t in prev:
        prev_tokens.update(_tokenize_text(t.get("text", "")))

    recent_tokens = set()
    for t in recent:
        recent_tokens.update(_tokenize_text(t.get("text", "")))

    new_tokens = [tok for tok in recent_tokens if tok not in prev_tokens]
    return len(new_tokens) < int(min_new_keywords)


def evaluate_proactive_trigger(
    turn_text,
    trigger_words=DEFAULT_TRIGGER_WORDS,
    struggle_cues=DEFAULT_PROACTIVE_STRUGGLE_CUES,
    conversation_events=None,
    last_trigger_epoch=None,
    current_epoch=None,
    cooldown_seconds=PROACTIVE_TRIGGER_COOLDOWN_SECONDS,
    score_threshold=PROACTIVE_TRIGGER_SCORE_THRESHOLD,
):
    score = 0.0
    reasons = []
    cooldown_active = False

    if last_trigger_epoch is not None and current_epoch is not None:
        try:
            cooldown_active = float(current_epoch) - float(last_trigger_epoch) < float(cooldown_seconds)
        except Exception:
            cooldown_active = False

    if cooldown_active:
        return {"triggered": False, "score": 0.0, "reasons": ["cooldown"], "cooldown_active": True}

    if text_contains_trigger_word(turn_text, trigger_words):
        score += PROACTIVE_TRIGGER_WEIGHTS["trigger_word"]
        reasons.append("trigger_word")

    if text_contains_proactive_cue(turn_text, struggle_cues):
        score += PROACTIVE_TRIGGER_WEIGHTS["struggle_cue"]
        reasons.append("struggle_cue")

    events = conversation_events if conversation_events is not None else GLOBAL_CONVERSATION_HISTORY
    try:
        if lacks_novelty(events):
            score += PROACTIVE_TRIGGER_WEIGHTS["low_novelty"]
            reasons.append("low_novelty")
    except Exception:
        pass

    return {
        "triggered": score >= float(score_threshold),
        "score": round(score, 2),
        "reasons": reasons,
        "cooldown_active": False,
    }


def resolve_live_audio_input(
    input_mode=DEFAULT_AUDIO_INPUT_MODE,
    audio_device=None,
    participant_channel_map=DEFAULT_FOCUSRITE_PARTICIPANTS,
):
    sd = load_sounddevice()
    selected_mode = (input_mode or DEFAULT_AUDIO_INPUT_MODE).strip().lower()

    if selected_mode == "focusrite":
        required_channels = max(int(channel) for channel, _speaker in participant_channel_map)
        device_index, device_info = resolve_input_device(
            sd,
            preferred_device=audio_device,
            patterns=FOCUSRITE_DEVICE_PATTERNS,
            min_input_channels=required_channels,
            mode_name="Focusrite",
            allow_default=False,
        )
        channel_map = tuple((int(channel), speaker) for channel, speaker in participant_channel_map)
    elif selected_mode == "laptop":
        device_index, device_info = resolve_input_device(
            sd,
            preferred_device=audio_device,
            patterns=LAPTOP_DEVICE_PATTERNS,
            min_input_channels=1,
            mode_name="laptop microphone",
            allow_default=True,
        )
        required_channels = 1
        channel_map = ((1, "Participant"),)
    else:
        raise RuntimeError(f"Unknown audio input mode: {input_mode}. Use focusrite or laptop.")

    return {
        "sounddevice": sd,
        "input_mode": selected_mode,
        "device_index": device_index,
        "device_info": device_info,
        "required_channels": required_channels,
        "channel_map": channel_map,
    }


def build_deepgram_live_receiver(
    api_key,
    endpoint,
    record_seconds=6.0,
    input_mode=DEFAULT_AUDIO_INPUT_MODE,
    audio_device=None,
    samplerate=None,
    silence_rms=120.0,
    participant_channel_map=DEFAULT_FOCUSRITE_PARTICIPANTS,
):
    audio_input = resolve_live_audio_input(
        input_mode=input_mode,
        audio_device=audio_device,
        participant_channel_map=participant_channel_map,
    )
    selected_mode = audio_input["input_mode"]
    device_index = audio_input["device_index"]
    device_info = audio_input["device_info"]
    required_channels = audio_input["required_channels"]
    channel_map = audio_input["channel_map"]

    if selected_mode == "focusrite":
        channel_map_text = ", ".join(f"input {channel} -> {speaker}" for channel, speaker in channel_map)
        print(f"Focusrite mode enabled: device {device_index}: {device_info.get('name', 'Focusrite')}")
        print(f"Focusrite participant map: {channel_map_text}")
    else:
        print(f"Laptop microphone mode enabled: device {device_index}: {device_info.get('name', 'default input')}")

    def receive():
        command = input("Press Enter to record the next participant segment, or type quit to stop: ").strip()
        if command.lower() in LIVE_EXIT_WORDS:
            return command

        if selected_mode == "focusrite":
            segment_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            recording, selected_samplerate = record_audio_array(
                duration=record_seconds,
                samplerate=samplerate,
                channels=required_channels,
                device=device_index,
                device_info=device_info,
            )
            participant_turns = []
            for channel_number, speaker in channel_map:
                channel_audio = extract_audio_channel(recording, channel_number)
                rms = calculate_audio_rms(channel_audio)
                if silence_rms and rms < float(silence_rms):
                    print(f"[Focusrite input {channel_number} / {speaker}] skipped as silence (RMS {rms:.1f})")
                    continue

                audio_bytes = encode_wav_bytes(channel_audio, selected_samplerate, 1)
                print(f"Sending Focusrite input {channel_number} ({speaker}) to Deepgram...")
                transcript = deepgram_transcribe_bytes(
                    audio_data=audio_bytes,
                    content_type="audio/wav",
                    api_key=api_key,
                    endpoint=endpoint,
                )
                print(f"[Deepgram:{speaker}:input {channel_number}] {transcript or '(no speech recognized)'}")
                if transcript:
                    participant_turns.append(make_participant_turn(
                        speaker,
                        transcript,
                        timestamp=segment_timestamp,
                        audio_source="focusrite",
                        audio_channel=int(channel_number),
                        audio_rms=round(rms, 2),
                    ))
            return participant_turns

        audio_bytes, selected_samplerate, channels = record_audio_from_mic(
            duration=record_seconds,
            samplerate=samplerate,
            channels=1,
            device=device_index,
            device_info=device_info,
        )
        print("Sending laptop microphone audio to Deepgram for transcription...")
        content_type = "audio/wav"
        transcript = deepgram_transcribe_bytes(
            audio_data=audio_bytes,
            content_type=content_type,
            api_key=api_key,
            endpoint=endpoint,
        )
        print(f"[Deepgram] Recognized speech: {transcript}")
        return transcript

    return receive


def build_audio_turn_request(payload, transcript, speaker="Participant"):
    participant_turn = make_participant_turn(speaker, transcript)
    request = dict(payload)
    request.update({
        "turn_index": request.get("turn_index", 1),
        "turn_timestamp": participant_turn["timestamp"],
        "last_user_utterance": format_participant_turns_text([participant_turn]),
        "recent_participant_turns": [participant_turn],
        "conversation_history": list(request.get("conversation_history", [])) + [participant_turn],
    })
    return request


class ContinuousSpeechSegmenter:
    def __init__(
        self,
        speaker,
        channel_number,
        samplerate,
        start_rms=400.0,
        stop_rms=220.0,
        end_silence_seconds=0.8,
        pre_roll_seconds=0.25,
        min_speech_seconds=0.35,
        max_segment_seconds=18.0,
    ):
        self.speaker = speaker
        self.channel_number = int(channel_number)
        self.samplerate = int(samplerate)
        self.start_rms = float(start_rms)
        self.stop_rms = float(stop_rms)
        self.end_silence_frames = int(float(end_silence_seconds) * self.samplerate)
        self.pre_roll_frames_limit = int(float(pre_roll_seconds) * self.samplerate)
        self.min_speech_frames = int(float(min_speech_seconds) * self.samplerate)
        self.max_segment_frames = int(float(max_segment_seconds) * self.samplerate)
        self.pre_roll = []
        self.pre_roll_frame_count = 0
        self.active = False
        self.frames = []
        self.frame_count = 0
        self.silence_frames = 0
        self.start_epoch = None
        self.peak_rms = 0.0

    def _append_pre_roll(self, chunk):
        self.pre_roll.append(chunk.copy())
        self.pre_roll_frame_count += len(chunk)
        while self.pre_roll and self.pre_roll_frame_count > self.pre_roll_frames_limit:
            removed = self.pre_roll.pop(0)
            self.pre_roll_frame_count -= len(removed)

    def _finish(self, end_epoch):
        if not self.active:
            return None

        frames = list(self.frames)
        frame_count = self.frame_count
        start_epoch = self.start_epoch
        peak_rms = self.peak_rms

        self.active = False
        self.frames = []
        self.frame_count = 0
        self.silence_frames = 0
        self.start_epoch = None
        self.peak_rms = 0.0

        if frame_count < self.min_speech_frames or not frames:
            return None

        import numpy as np
        audio = np.concatenate(frames, axis=0)
        return {
            "speaker": self.speaker,
            "audio_channel": self.channel_number,
            "start_epoch": start_epoch,
            "end_epoch": end_epoch,
            "start_timestamp": timestamp_from_epoch(start_epoch),
            "end_timestamp": timestamp_from_epoch(end_epoch),
            "duration_seconds": round(max(0.0, float(end_epoch) - float(start_epoch)), 3),
            "audio_rms": round(peak_rms, 2),
            "audio": audio,
        }

    def process(self, chunk, chunk_start_epoch):
        chunk = chunk.copy()
        chunk_frames = len(chunk)
        chunk_end_epoch = float(chunk_start_epoch) + (chunk_frames / self.samplerate)
        rms = calculate_audio_rms(chunk)

        if not self.active:
            self._append_pre_roll(chunk)
            if rms < self.start_rms:
                return None

            self.active = True
            self.frames = list(self.pre_roll)
            self.frame_count = self.pre_roll_frame_count
            pre_roll_seconds = max(0.0, (self.pre_roll_frame_count - chunk_frames) / self.samplerate)
            self.start_epoch = max(0.0, float(chunk_start_epoch) - pre_roll_seconds)
            self.silence_frames = 0
            self.peak_rms = rms
            self.pre_roll = []
            self.pre_roll_frame_count = 0
            return None

        self.frames.append(chunk)
        self.frame_count += chunk_frames
        self.peak_rms = max(self.peak_rms, rms)

        if rms >= self.stop_rms:
            self.silence_frames = 0
        else:
            self.silence_frames += chunk_frames

        if self.silence_frames >= self.end_silence_frames or self.frame_count >= self.max_segment_frames:
            return self._finish(chunk_end_epoch)

        return None

    def flush(self):
        if not self.active:
            return None
        end_epoch = time.time()
        return self._finish(end_epoch)


class ContinuousAudioTranscriber:
    def __init__(
        self,
        api_key,
        endpoint,
        input_mode=DEFAULT_AUDIO_INPUT_MODE,
        audio_device=None,
        samplerate=None,
        participant_channel_map=DEFAULT_FOCUSRITE_PARTICIPANTS,
        vad_start_rms=400.0,
        vad_stop_rms=220.0,
        vad_end_silence_seconds=0.8,
        vad_pre_roll_seconds=0.25,
        vad_min_speech_seconds=0.35,
        vad_max_segment_seconds=18.0,
        blocksize=1024,
        transcribe_workers=2,
    ):
        audio_input = resolve_live_audio_input(
            input_mode=input_mode,
            audio_device=audio_device,
            participant_channel_map=participant_channel_map,
        )
        self.sd = audio_input["sounddevice"]
        self.api_key = api_key
        self.endpoint = endpoint
        self.input_mode = audio_input["input_mode"]
        self.device_index = audio_input["device_index"]
        self.device_info = audio_input["device_info"]
        self.required_channels = audio_input["required_channels"]
        self.channel_map = audio_input["channel_map"]
        self.samplerate = select_samplerate(self.device_info, samplerate)
        self.blocksize = int(blocksize)
        self.segment_queue = queue.Queue()
        self.transcript_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.stream = None
        self.worker_threads = []
        self.transcribe_workers = max(1, int(transcribe_workers))
        self.segmenters = [
            ContinuousSpeechSegmenter(
                speaker=speaker,
                channel_number=channel_number,
                samplerate=self.samplerate,
                start_rms=vad_start_rms,
                stop_rms=vad_stop_rms,
                end_silence_seconds=vad_end_silence_seconds,
                pre_roll_seconds=vad_pre_roll_seconds,
                min_speech_seconds=vad_min_speech_seconds,
                max_segment_seconds=vad_max_segment_seconds,
            )
            for channel_number, speaker in self.channel_map
        ]

    @property
    def has_active_speech(self):
        return any(segmenter.active for segmenter in self.segmenters)

    @property
    def device_name(self):
        return self.device_info.get("name", "selected input")

    def start(self):
        if self.stream:
            return

        self.stop_event.clear()
        for index in range(self.transcribe_workers):
            thread = threading.Thread(target=self._transcribe_worker, name=f"deepgram_worker_{index + 1}", daemon=True)
            thread.start()
            self.worker_threads.append(thread)

        self.stream = self.sd.InputStream(
            samplerate=self.samplerate,
            channels=self.required_channels,
            dtype="int16",
            device=self.device_index,
            blocksize=self.blocksize,
            callback=self._audio_callback,
        )
        self.stream.start()

        if self.input_mode == "focusrite":
            channel_map_text = ", ".join(f"input {channel} -> {speaker}" for channel, speaker in self.channel_map)
            print(f"Continuous Focusrite input: device {self.device_index}: {self.device_name}")
            print(f"Focusrite participant map: {channel_map_text}")
        else:
            print(f"Continuous laptop microphone input: device {self.device_index}: {self.device_name}")

    def stop(self):
        self.stop_event.set()
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        for segmenter in self.segmenters:
            segment = segmenter.flush()
            if segment:
                self.segment_queue.put(segment)

        for _index in range(self.transcribe_workers):
            self.segment_queue.put(None)

        for thread in self.worker_threads:
            thread.join(timeout=2)
        self.worker_threads = []

    def get_event(self, timeout=0.1):
        try:
            return self.transcript_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            self.transcript_queue.put({
                "type": "warning",
                "message": str(status),
                "timestamp": timestamp_from_epoch(),
            })

        chunk_start_epoch = time.time() - (float(frames) / self.samplerate)
        audio_chunk = indata.copy()
        for segmenter in self.segmenters:
            try:
                channel_audio = extract_audio_channel(audio_chunk, segmenter.channel_number)
                segment = segmenter.process(channel_audio, chunk_start_epoch)
                if segment:
                    segment["audio_source"] = self.input_mode
                    segment["audio_device"] = self.device_name
                    self.segment_queue.put(segment)
            except Exception as error:
                self.transcript_queue.put({
                    "type": "error",
                    "message": f"Audio segmentation failed on channel {segmenter.channel_number}: {error}",
                    "timestamp": timestamp_from_epoch(),
                })

    def _transcribe_worker(self):
        while True:
            segment = self.segment_queue.get()
            if segment is None:
                break

            try:
                audio_bytes = encode_wav_bytes(segment["audio"], self.samplerate, 1)
                transcript = deepgram_transcribe_bytes(
                    audio_data=audio_bytes,
                    content_type="audio/wav",
                    api_key=self.api_key,
                    endpoint=self.endpoint,
                )
            except Exception as error:
                self.transcript_queue.put({
                    "type": "error",
                    "message": f"Deepgram transcription failed for {segment['speaker']}: {error}",
                    "timestamp": timestamp_from_epoch(),
                    "speaker": segment.get("speaker"),
                    "start_timestamp": segment.get("start_timestamp"),
                    "end_timestamp": segment.get("end_timestamp"),
                })
                continue

            transcript = (transcript or "").strip()
            if not transcript:
                self.transcript_queue.put({
                    "type": "empty",
                    "speaker": segment["speaker"],
                    "timestamp": timestamp_from_epoch(),
                    "start_timestamp": segment["start_timestamp"],
                    "end_timestamp": segment["end_timestamp"],
                    "audio_channel": segment["audio_channel"],
                    "audio_rms": segment["audio_rms"],
                })
                continue

            turn = make_participant_turn(
                segment["speaker"],
                transcript,
                timestamp=segment["end_timestamp"],
                start_timestamp=segment["start_timestamp"],
                end_timestamp=segment["end_timestamp"],
                duration_seconds=segment["duration_seconds"],
                audio_source=segment["audio_source"],
                audio_device=segment["audio_device"],
                audio_channel=segment["audio_channel"],
                audio_rms=segment["audio_rms"],
            )
            self.transcript_queue.put({"type": "turn", "turn": turn})


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
        self.tts.setLanguage(self.language)
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

    def set_vocal_params(self, speed=100, volume=0.7, pitch=1.0):
        """Set vocal delivery parameters for speech.
        
        Args:
            speed: Speech rate percentage; 100 is Pepper's default
            volume: Volume level (0.0-1.0, default 0.7)
            pitch: Best-effort pitch level for NAOqi versions that support it
        """
        if not self.tts:
            return
        try:
            speed_value = float(speed)
            if speed_value <= 2.0:
                speed_value *= 100.0
            self.tts.setParameter("speed", speed_value)
            self.tts.setVolume(float(volume))
            try:
                self.tts.setParameter("pitch", float(pitch))
            except Exception:
                if float(pitch) >= 1.0:
                    self.tts.setParameter("pitchShift", float(pitch))
        except Exception as e:
            # Silently fail if parameters not supported in this NAOqi version
            pass

    def say(self, text, style="supportive"):
        if not text:
            return
        if self.tts:
            # Apply vocal parameters based on style
            if style in VOCAL_DELIVERY:
                params = VOCAL_DELIVERY[style]
                self.set_vocal_params(**params)
            self.tts.say(text)
            # Reset to default after speaking
            self.set_vocal_params()

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


def vocal_params_for_style(style):
    return dict(VOCAL_DELIVERY.get(style) or VOCAL_DELIVERY[MODE_DEFAULTS["style"]])


def send_to_pepper_via_py27(text, ip, port, script_path, python_cmd, speed=100, volume=0.7, pitch=1.0):
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
        "--speed",
        str(speed),
        "--volume",
        str(volume),
        "--pitch",
        str(pitch),
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

def build_naoqi_subprocess_env():
    env = os.environ.copy()
    sdk_root = env.get("NAOQI_SDK_ROOT") or DEFAULT_NAOQI_SDK_ROOT
    sdk_lib = str(pathlib.Path(sdk_root) / "lib")

    env.setdefault("NAOQI_SDK_ROOT", sdk_root)
    if pathlib.Path(sdk_lib).exists():
        existing_pythonpath = env.get("NAOQI_PYTHONPATH", "")
        pythonpath_parts = [item for item in existing_pythonpath.split(os.pathsep) if item]
        if sdk_lib not in pythonpath_parts:
            pythonpath_parts.insert(0, sdk_lib)
        env["NAOQI_PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

        path_parts = [item for item in env.get("PATH", "").split(os.pathsep) if item]
        if sdk_lib not in path_parts:
            path_parts.insert(0, sdk_lib)
        env["PATH"] = os.pathsep.join(path_parts)

    return env

def check_tcp_port(host, port, timeout=1.5):
    try:
        with socket.create_connection((str(host), int(port)), timeout=float(timeout)):
            return True
    except OSError:
        return False


def make_resilient_sender(sender, label="Pepper TTS"):
    failed_once = {"value": False}

    def resilient_sender(text, style="supportive"):
        try:
            sender(text, style=style)
        except Exception as error:
            if not failed_once["value"]:
                print(f"Warning: {label} failed; continuing without stopping the live listener: {error}")
                failed_once["value"] = True
            else:
                print(f"Warning: {label} failed again; continuing.")

    return resilient_sender


def build_pepper_tts_sender(args):
    if not check_tcp_port(args.pepper_ip, args.pepper_port):
        print(
            f"Warning: Pepper is not reachable at {args.pepper_ip}:{args.pepper_port}. "
            "The live listener will keep running; use --pepper-ip if Pepper has a different address."
        )

    if ALProxy is None:
        legacy_python_cmd = args.pepper_legacy_python.strip().split()
        legacy_script = pathlib.Path(args.pepper_legacy_tts_script)
        if not legacy_script.is_absolute():
            legacy_script = (ROOT.parent / legacy_script).resolve()

        if not legacy_script.exists():
            raise RuntimeError(f"Legacy TTS script not found: {legacy_script}")

        print("naoqi SDK unavailable in Python 3; using Python 2.7 Pepper TTS bridge.")

        def sender(text, style="supportive"):
            params = vocal_params_for_style(style)
            send_to_pepper_via_py27(
                text=text,
                ip=args.pepper_ip,
                port=args.pepper_port,
                script_path=legacy_script,
                python_cmd=legacy_python_cmd,
                **params,
            )

        return make_resilient_sender(sender), None

    pepper = PepperIO(
        ip=args.pepper_ip,
        port=args.pepper_port,
        language=args.pepper_language,
        vocabulary=parse_phrase_list(args.pepper_vocabulary),
    )
    pepper.connect()
    print(f"Connected to Pepper at {args.pepper_ip}:{args.pepper_port}")

    def sender(text, style="supportive"):
        pepper.say(text, style=style)

    return make_resilient_sender(sender), pepper


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
        speaker = item.get("speaker", "Participant")
        text = item.get("text", "")
        lines.append(f"{speaker}: {text}".strip())
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


def get_optional_mode_value(payload, key):
    selected = payload.get("mode_combo", {}).get(key, MODE_DEFAULTS[key])
    if selected in {None, "", "off"}:
        return None
    return get_mode_value(payload, key)


def get_delivery_style(payload):
    return get_optional_mode_value(payload, "style") or MODE_DEFAULTS["style"]


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
    style_key = get_optional_mode_value(payload, "style")
    initiative_key = get_optional_mode_value(payload, "initiative")
    role_key = get_optional_mode_value(payload, "role")
    seed_ideas = payload.get("seed_ideas", [])
    seed_text = "; ".join(seed_ideas) if seed_ideas else "none"
    history_window_turns = int(payload.get("history_window_turns", 10))
    history_text = format_history_window(payload.get("conversation_history", []), history_window_turns)
    pending_turns = payload.get("recent_participant_turns", [])
    pending_text = "\n".join(
        f"{item.get('speaker', 'Participant')}: {item.get('text', '')}".strip()
        for item in pending_turns
    ) if pending_turns else "none"

    guidance = [
        BASE_SETUP,
        f"Elicitation mode guidance: {ELICITATION_SETUP[elicitation_key]}",
    ]
    if style_key:
        guidance.append(f"Style guidance: {STYLE_SETUP[style_key]}")
    if initiative_key:
        guidance.append(f"Initiative guidance: {INITIATIVE_SETUP[initiative_key]}")
    if role_key:
        guidance.append(f"Role guidance: {ROLE_SETUP[role_key]}")
    system_text = " ".join(guidance)

    user_text = (
        f"Theme: {payload.get('theme', '')}\n"
        f"Phase: {payload.get('phase', '')}\n"
        f"Recent conversation history:\n{history_text}\n"
        f"Latest uninterrupted participant turns:\n{pending_text}\n"
        f"Last participant utterance: {payload.get('last_user_utterance', '')}\n"
        f"Seed ideas: {seed_text}\n"
        f"Intervention prompt to adapt briefly to this moment: {prompt_row['text']}\n"
        f"Bridge from what the participants just said before using the intervention.\n"
        f"{REPLY_CONTRACT}"
    )

    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def build_messages_context_only(payload):
    style_key = get_optional_mode_value(payload, "style")
    initiative_key = get_optional_mode_value(payload, "initiative")
    role_key = get_optional_mode_value(payload, "role")
    seed_ideas = payload.get("seed_ideas", [])
    seed_text = "; ".join(seed_ideas) if seed_ideas else "none"
    history_window_turns = int(payload.get("history_window_turns", 10))
    history_text = format_history_window(payload.get("conversation_history", []), history_window_turns)
    pending_turns = payload.get("recent_participant_turns", [])
    pending_text = "\n".join(
        f"{item.get('speaker', 'Participant')}: {item.get('text', '')}".strip()
        for item in pending_turns
    ) if pending_turns else "none"

    guidance = [BASE_SETUP]
    if style_key:
        guidance.append(f"Style guidance: {STYLE_SETUP[style_key]}")
    if initiative_key:
        guidance.append(f"Initiative guidance: {INITIATIVE_SETUP[initiative_key]}")
    if role_key:
        guidance.append(f"Role guidance: {ROLE_SETUP[role_key]}")
    guidance.append("No predefined intervention prompt is active. Respond only based on conversation context.")
    system_text = " ".join(guidance)

    user_text = (
        f"Theme: {payload.get('theme', '')}\n"
        f"Phase: {payload.get('phase', '')}\n"
        f"Recent conversation history:\n{history_text}\n"
        f"Latest uninterrupted participant turns:\n{pending_text}\n"
        f"Last participant utterance: {payload.get('last_user_utterance', '')}\n"
        f"Seed ideas: {seed_text}\n"
        f"Respond directly to the latest participant request or question.\n"
        f"{REPLY_CONTRACT}"
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
        "stop": payload.get("stop", DEFAULT_LM_STOP_SEQUENCES),
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
    if payload.get("fallback_prompt"):
        return sanitize_reply(payload["fallback_prompt"]) or "What should we focus on next from what you just said?"

    strategy = normalize_elicitation(prompt_row.get("strategy", ""))
    if strategy == "perspective_shift":
        return "How might this feel from a student's point of view?"
    if strategy == "generative":
        return "What is one more option we could add?"
    if strategy == "elaboration_evidence":
        return "What would make this idea concrete enough to test?"
    return "What should we focus on next from what you just said?"


def truncate_reply(line, max_words=DEFAULT_REPLY_MAX_WORDS):
    line = " ".join((line or "").strip().split())
    if not line:
        return ""

    words = line.split()
    if len(words) > int(max_words):
        line = " ".join(words[:int(max_words)]).rstrip(" ,;:")
        if line and line[-1] not in ".!?":
            line += "."

    return line.strip()


def sanitize_reply(text, max_words=DEFAULT_REPLY_MAX_WORDS):
    raw = (text or "").strip()
    if not raw:
        return ""

    raw = re.sub(r"^\s*(?:Pepper|Robot|Assistant)\s*:\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^\s*\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?\s*", "", raw)
    raw = re.sub(r"^\s*\[?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?\]?\s*", "", raw)

    split_patterns = [
        r"\n\s*---",
        r"\s+---\s+",
        r"\s+\*\*?\s*Note\s*:",
        r"\s+Note\s*:",
        r"\s+(?:Pepper|Robot|Assistant)\s*:",
        r"\s+Participant\s*\d*\s*:",
        r"\n\s*(?:Pepper|Robot|Assistant|Participant\s*\d*)\s*:",
    ]
    for pattern in split_patterns:
        raw = re.split(pattern, raw, maxsplit=1, flags=re.IGNORECASE)[0].strip()

    line = " ".join(raw.split())
    line = line.replace("**", "").replace("__", "")
    line = re.sub(r"^[\-*•]\s*", "", line)
    line = re.sub(r"[#/]{2,}", " ", line)
    line = re.sub(r"[=]{1,}/\w+", "", line)
    line = " ".join(line.split())
    lower = line.lower()
    
    reasoning_markers = [
        "thinking process",
        "analyze the request",
        "analyze the context",
        "analyze role",
        "internal reasoning",
        "response:",
        "the conversation has been crafted",
        "given instructions",
        "according to the given instructions",
        "as pepper",
        "pepper:",
        "robot:",
        "participant:",
        "multiple possible",
        "dialogue cycle",
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
    
    meta_starts = ["i need to", "the user wants", "i should", "we need to", "here are", "sure, here"]
    if any(lower.startswith(prefix) for prefix in meta_starts):
        return ""

    return truncate_reply(line, max_words=max_words)


def process(payload):
    prompt_bank = load_prompt_bank()
    prompt_row, elicitation_key = select_prompt(payload, prompt_bank)
    messages = build_messages(payload, prompt_row, elicitation_key)

    fallback_reason = ""
    try:
        raw_reply = call_lmstudio(payload, messages)
        reply = sanitize_reply(
            raw_reply,
            max_words=int(payload.get("reply_max_words", DEFAULT_REPLY_MAX_WORDS)),
        )
        if not reply:
            print(f"Debug: LM Studio raw reply before sanitization: {raw_reply!r}")
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

    style = get_delivery_style(payload)
    return {
        "ok": True,
        "source": source,
        "reply": reply,
        "prompt_id": prompt_row["prompt_id"],
        "strategy": prompt_row["strategy"],
        "phase": prompt_row["phase"],
        "prompt_text": prompt_row["text"],
        "fallback_reason": fallback_reason,
        "style": style,
    }


def process_context_only(payload):
    messages = build_messages_context_only(payload)
    fallback_reason = ""
    fallback_text = (
        sanitize_reply(payload.get("context_fallback") or "What should we focus on next from what you just said?")
        or "What should we focus on next from what you just said?"
    )

    try:
        raw_reply = call_lmstudio(payload, messages)
        reply = sanitize_reply(
            raw_reply,
            max_words=int(payload.get("reply_max_words", DEFAULT_REPLY_MAX_WORDS)),
        )
        if not reply:
            print(f"Debug: LM Studio raw reply before sanitization: {raw_reply!r}")
            raise ValueError("Unusable LM Studio reply")
        source = "lmstudio"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
        fallback_reason = f"{type(error).__name__}: {error}"
        reply = fallback_text
        source = "fallback"

    style = get_delivery_style(payload)
    return {
        "ok": True,
        "source": source,
        "reply": reply,
        "prompt_id": "",
        "strategy": "context_only",
        "phase": payload.get("phase", "divergence"),
        "prompt_text": "",
        "fallback_reason": fallback_reason,
        "style": style,
    }


def append_transcript_event(transcript_log_path, payload, event):
    if not transcript_log_path:
        return

    path = resolve_log_path(transcript_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = (not path.exists()) or path.stat().st_size == 0

    row = {
        "session_id": payload.get("session_id", "S01"),
        "group_id": payload.get("group_id", "G01"),
        "conversation_id": payload.get("conversation_id", payload.get("session_id", "S01")),
        "sequence_index": event.get("sequence_index", ""),
        "robot_turn_index": event.get("robot_turn_index", ""),
        "event_type": event.get("event_type", ""),
        "timestamp": event.get("timestamp") or event.get("end_timestamp") or timestamp_from_epoch(),
        "start_timestamp": event.get("start_timestamp", ""),
        "end_timestamp": event.get("end_timestamp", ""),
        "speaker": event.get("speaker", ""),
        "text": event.get("text", ""),
        "phase": event.get("phase", payload.get("phase", "")),
        "strategy": event.get("strategy", ""),
        "prompt_id": event.get("prompt_id", ""),
        "source": event.get("source", ""),
        "audio_input_mode": event.get("audio_input_mode", payload.get("audio_input_mode", "")),
        "audio_device": event.get("audio_device", payload.get("audio_device", "")),
        "audio_source": event.get("audio_source", ""),
        "audio_channel": event.get("audio_channel", ""),
        "audio_rms": event.get("audio_rms", ""),
        "triggered_robot": event.get("triggered_robot", ""),
        "trigger_words": event.get("trigger_words", ""),
        "fallback_reason": event.get("fallback_reason", ""),
        "elicitation_engagement_score": event.get("elicitation_engagement_score", ""),
        "creative_confidence_score": event.get("creative_confidence_score", ""),
        "evaluation_moment": event.get("evaluation_moment", ""),
        "previous_elicitation_prompt_id": event.get("previous_elicitation_prompt_id", ""),
        "previous_elicitation_strategy": event.get("previous_elicitation_strategy", ""),
        "previous_elicitation_phase": event.get("previous_elicitation_phase", ""),
    }

    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRANSCRIPT_LOG_COLUMNS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)

        # Update in-memory conversation history for novelty detection (bounded)
        try:
            GLOBAL_CONVERSATION_HISTORY.append({
                "speaker": row.get("speaker", ""),
                "text": row.get("text", ""),
                "timestamp": row.get("timestamp", ""),
            })
            if len(GLOBAL_CONVERSATION_HISTORY) > 500:
                del GLOBAL_CONVERSATION_HISTORY[: len(GLOBAL_CONVERSATION_HISTORY) - 500]
        except Exception:
            pass


def append_participant_transcript_event(transcript_log_path, payload, turn, sequence_index, triggered_robot=False, trigger_words=DEFAULT_TRIGGER_WORDS, robot_turn_index=""):
    append_transcript_event(
        transcript_log_path,
        payload,
        {
            "sequence_index": sequence_index,
            "robot_turn_index": robot_turn_index,
            "event_type": "participant",
            "timestamp": turn.get("timestamp", ""),
            "start_timestamp": turn.get("start_timestamp", turn.get("timestamp", "")),
            "end_timestamp": turn.get("end_timestamp", turn.get("timestamp", "")),
            "speaker": turn.get("speaker", "Participant"),
            "text": turn.get("text", ""),
            "audio_input_mode": payload.get("audio_input_mode", ""),
            "audio_device": turn.get("audio_device", payload.get("audio_device", "")),
            "audio_source": turn.get("audio_source", ""),
            "audio_channel": turn.get("audio_channel", ""),
            "audio_rms": turn.get("audio_rms", ""),
            "triggered_robot": "true" if triggered_robot else "false",
            "trigger_words": ",".join(parse_trigger_words(trigger_words)),
        },
    )


def append_robot_transcript_event(
    transcript_log_path,
    payload,
    result,
    sequence_index,
    robot_turn_index,
    robot_timestamp=None,
    robot_start_timestamp=None,
    robot_end_timestamp=None,
):
    if robot_start_timestamp is None:
        robot_start_timestamp = robot_timestamp or timestamp_from_epoch()
    if robot_end_timestamp is None:
        robot_end_timestamp = robot_timestamp or robot_start_timestamp
    append_transcript_event(
        transcript_log_path,
        payload,
        {
            "sequence_index": sequence_index,
            "robot_turn_index": robot_turn_index,
            "event_type": "robot",
            "timestamp": robot_end_timestamp,
            "start_timestamp": robot_start_timestamp,
            "end_timestamp": robot_end_timestamp,
            "speaker": "Robot",
            "text": result.get("reply", ""),
            "phase": result.get("phase", payload.get("phase", "")),
            "strategy": result.get("strategy", ""),
            "prompt_id": result.get("prompt_id", ""),
            "source": result.get("source", ""),
            "triggered_robot": "true",
            "fallback_reason": result.get("fallback_reason", ""),
            "elicitation_engagement_score": result.get("elicitation_engagement_score", ""),
            "creative_confidence_score": result.get("creative_confidence_score", ""),
            "evaluation_moment": result.get("evaluation_moment", ""),
            "previous_elicitation_prompt_id": result.get("previous_elicitation_prompt_id", ""),
            "previous_elicitation_strategy": result.get("previous_elicitation_strategy", ""),
            "previous_elicitation_phase": result.get("previous_elicitation_phase", ""),
        },
    )


def append_log_conversation_header(handle, session_id, group_id, conversation_id):
    handle.write(f"{session_id},{group_id},{conversation_id}\n")
    handle.write("\n")


def append_log_turn_block(log_path, payload, result, participant_turns, robot_timestamp):
    if not log_path:
        return

    path = resolve_log_path(log_path)
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
    print(f"[Robot:{result['source']}/{result.get('prompt_id', '')}] {result['reply']}")
    if result.get('source') == 'fallback' and result.get('fallback_reason'):
        print(f"[Fallback reason] {result['fallback_reason']}")


def print_robot_trigger_reason(trigger_reason=None, trigger_reasons=None):
    reasons = []
    if trigger_reasons:
        reasons.extend(str(reason).strip() for reason in trigger_reasons if str(reason).strip())
    elif trigger_reason:
        reason_text = str(trigger_reason).strip()
        if reason_text:
            reasons.append(reason_text)

    if not reasons:
        reasons = ["manual"]

    deduped = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)

    print(f"[Robot trigger] reason={', '.join(deduped)}")


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

        if speaker.lower() in {"robot", "pepper", "paper"}:
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
            if "pepper" in last_text or "robot" in last_text or "paper" in last_text:
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
                    last_epoch = epoch_from_timestamp(last_ts)
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


def console_send(text, style="supportive"):
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
    transcript_log_path = base_payload.get("transcript_log_path", DEFAULT_TRANSCRIPT_LOG_PATH)
    sequence_index = 0
    turn_index = 0

    while True:
        received = receive_fn()
        if isinstance(received, str) and received.strip().lower() in LIVE_EXIT_WORDS:
            print("--- Live dialog stopped ---")
            break

        participant_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        participant_turns = coerce_participant_turns(received, timestamp=participant_timestamp)
        if not participant_turns:
            continue

        if len(participant_turns) == 1 and participant_turns[0]["text"].lower() in LIVE_EXIT_WORDS:
            print("--- Live dialog stopped ---")
            break

        for participant_turn in participant_turns:
            sequence_index += 1
            conversation_history.append(participant_turn)
            print(f"[{participant_turn['speaker']}] {participant_turn['text']}")
            append_participant_transcript_event(
                transcript_log_path,
                base_payload,
                participant_turn,
                sequence_index=sequence_index,
                triggered_robot=True,
            )

        turn_index += 1
        request = dict(base_payload)
        request.update({
            "turn_index": turn_index,
            "turn_timestamp": participant_turns[0].get("timestamp", participant_timestamp),
            "last_user_utterance": format_participant_turns_text(participant_turns),
            "recent_participant_turns": participant_turns,
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
        send_fn(result["reply"], style=result.get("style", MODE_DEFAULTS["style"]))
        sequence_index += 1
        append_robot_transcript_event(
            transcript_log_path,
            request,
            result,
            sequence_index=sequence_index,
            robot_turn_index=turn_index,
            robot_timestamp=robot_timestamp,
        )
        append_log_turn_block(
            base_payload.get("log_path"),
            request,
            result,
            participant_turns,
            robot_timestamp,
        )


def run_continuous_live_dialog(
    base_payload,
    listener,
    send_fn,
    trigger_words=DEFAULT_TRIGGER_WORDS,
    elicitation_mode="off",
    intervention_every=4,
    keyboard_controls=True,
    proactive_silence_threshold=PROACTIVE_SILENCE_THRESHOLD,
    evaluation_elicitation=False,
):
    print("--- Continuous live dialog mode started ---")
    print("Microphones are live. Say Pepper or Paper to trigger the robot. Press Ctrl+C to stop.")

    conversation_history = base_payload.setdefault("conversation_history", [])
    pending_participant_turns = []
    transcript_log_path = base_payload.get("transcript_log_path", DEFAULT_TRANSCRIPT_LOG_PATH)
    sequence_index = 0
    robot_turn_index = 0
    trigger_words = parse_trigger_words(trigger_words)
    current_phase = base_payload.get("phase", "divergence")
    elicitation_mode = (elicitation_mode or "off").strip().lower()
    intervention_every = max(1, int(intervention_every or 1))
    prompt_bank = load_prompt_bank()
    strategy_sequences = safe_strategy_sequences(base_payload.get("group_id", ""), base_payload.get("theme_id", ""))
    used_strategies = {"divergence": [], "convergence": []}
    phase_reply_count = {"divergence": 0, "convergence": 0}
    command_queue = queue.Queue()
    stop_event = threading.Event()
    last_proactive_epoch = 0.0
    silence_started_epoch = None
    last_elicitation_result = None
    session_start_evaluation_recorded = False
    final_evaluation_recorded = False
    last_robot_reply_epoch = time.time()

    base_payload["phase"] = current_phase
    print_live_control_summary(base_payload, elicitation_mode, intervention_every, keyboard_controls)

    if keyboard_controls:
        start_live_command_reader(command_queue, stop_event)

    def current_initiative():
        return base_payload.get("mode_combo", {}).get("initiative", "reactive")

    def maybe_trigger_proactive_silence(trigger_timestamp=None):
        nonlocal silence_started_epoch, last_proactive_epoch, last_robot_reply_epoch
        if current_initiative() != "proactive":
            silence_started_epoch = None
            return False

        now_epoch = float(trigger_timestamp or time.time())
        cooldown_elapsed = now_epoch - last_proactive_epoch

        if listener.has_active_speech:
            silence_started_epoch = None
            return False

        if pending_participant_turns:
            if silence_started_epoch is None:
                silence_started_epoch = now_epoch

            elapsed = now_epoch - silence_started_epoch
            if elapsed >= float(proactive_silence_threshold) and cooldown_elapsed >= float(PROACTIVE_TRIGGER_COOLDOWN_SECONDS):
                last_proactive_epoch = now_epoch
                silence_started_epoch = None
                trigger_robot({"timestamp": timestamp_from_epoch(now_epoch)}, trigger_reason="silence")
                return True
            return False

        if now_epoch - last_robot_reply_epoch >= float(ROBOT_IDLE_REPLY_THRESHOLD_SECONDS) and cooldown_elapsed >= float(PROACTIVE_TRIGGER_COOLDOWN_SECONDS):
            last_proactive_epoch = now_epoch
            silence_started_epoch = None
            trigger_robot({"timestamp": timestamp_from_epoch(now_epoch)}, trigger_reason="silence", allow_empty=True)
            return True

        return False

    def refresh_strategy_sequences():
        nonlocal strategy_sequences, used_strategies
        strategy_sequences = safe_strategy_sequences(base_payload.get("group_id", ""), base_payload.get("theme_id", ""))
        used_strategies = {"divergence": [], "convergence": []}

    def evaluation_input_queue():
        return command_queue if keyboard_controls else None

    def record_session_start_evaluation():
        nonlocal sequence_index, session_start_evaluation_recorded
        if session_start_evaluation_recorded or not evaluation_elicitation:
            return
        if elicitation_mode == "off":
            return

        engagement_score = prompt_start_elicitation_engagement(
            input_queue=evaluation_input_queue(),
            stop_event=stop_event,
        )
        if stop_event.is_set():
            return

        confidence_score = prompt_creative_confidence(
            "start",
            input_queue=evaluation_input_queue(),
            stop_event=stop_event,
        )
        if stop_event.is_set() or (engagement_score == "" and confidence_score == ""):
            session_start_evaluation_recorded = True
            return

        now = timestamp_from_epoch()
        sequence_index += 1
        append_transcript_event(
            transcript_log_path,
            base_payload,
            {
                "sequence_index": sequence_index,
                "event_type": "session_start_evaluation",
                "timestamp": now,
                "start_timestamp": now,
                "end_timestamp": now,
                "speaker": "Researcher",
                "text": "Session-start engagement and creative confidence scores",
                "phase": current_phase,
                "strategy": "session_start",
                "source": "manual_evaluation",
                "elicitation_engagement_score": engagement_score,
                "creative_confidence_score": confidence_score,
                "evaluation_moment": "start",
            },
        )
        session_start_evaluation_recorded = True

    def build_robot_result(request):
        mode = (elicitation_mode or "off").strip().lower()
        result = None

        if mode == "scheduled":
            next_reply_index = phase_reply_count[current_phase] + 1
            schedule_slot = (next_reply_index % intervention_every == 0)
            if schedule_slot:
                planned = strategy_sequences.get(current_phase, [])
                next_strategy = None
                for strategy in planned:
                    strategy = normalize_elicitation(strategy)
                    if strategy and strategy not in used_strategies[current_phase]:
                        next_strategy = strategy
                        break

                if next_strategy:
                    prompt_row = select_first_prompt_for_strategy(prompt_bank, next_strategy, current_phase)
                    if prompt_row:
                        request["mode_combo"] = dict(request.get("mode_combo", {}))
                        request["mode_combo"]["elicitation"] = next_strategy
                        request["prompt_id"] = prompt_row["prompt_id"]
                        result = process(request)
                        used_strategies[current_phase].append(next_strategy)

        elif mode != "off":
            strategy = normalize_elicitation(mode)
            prompt_row = select_first_prompt_for_strategy(prompt_bank, strategy, current_phase)
            if prompt_row:
                request["mode_combo"] = dict(request.get("mode_combo", {}))
                request["mode_combo"]["elicitation"] = strategy
                request["prompt_id"] = prompt_row["prompt_id"]
                result = process(request)
            else:
                print(f"Warning: no prompt found for {strategy}/{current_phase}; using context-only response.")

        if result is None:
            request["prompt_id"] = ""
            result = process_context_only(request)

        return result

    def trigger_robot(triggering_turn, trigger_reason=None, trigger_reasons=None, allow_empty=False):
        nonlocal sequence_index, robot_turn_index, pending_participant_turns, last_elicitation_result, last_robot_reply_epoch, silence_started_epoch
        if not pending_participant_turns and not allow_empty:
            return

        robot_turn_index += 1
        participant_turns_for_reply = list(pending_participant_turns)
        request = dict(base_payload)
        request.update({
            "turn_index": robot_turn_index,
            "turn_timestamp": triggering_turn.get("timestamp", timestamp_from_epoch()),
            "phase": current_phase,
            "last_user_utterance": format_participant_turns_text(participant_turns_for_reply) if participant_turns_for_reply else request.get("context_fallback", "What should we focus on next from what you just said?"),
            "recent_participant_turns": participant_turns_for_reply,
            "conversation_history": conversation_history,
        })

        result = process_context_only(request) if allow_empty and not participant_turns_for_reply else build_robot_result(request)
        result_is_elicitation = is_elicitation_result(result)
        if evaluation_elicitation and result_is_elicitation and last_elicitation_result:
            score = prompt_previous_elicitation_engagement(
                last_elicitation_result,
                input_queue=evaluation_input_queue(),
                stop_event=stop_event,
            )
            attach_previous_elicitation_engagement(result, last_elicitation_result, score)
            if stop_event.is_set():
                return

        robot_start_timestamp = timestamp_from_epoch()

        robot_turn = {
            "speaker": "Robot",
            "text": result["reply"],
            "timestamp": robot_start_timestamp,
        }
        conversation_history.append(robot_turn)

        print_robot_trigger_reason(trigger_reason=trigger_reason, trigger_reasons=trigger_reasons)
        print_robot_turn(result)
        send_fn(result["reply"], style=result.get("style", MODE_DEFAULTS["style"]))
        robot_end_timestamp = timestamp_from_epoch()
        last_robot_reply_epoch = time.time()
        sequence_index += 1
        append_robot_transcript_event(
            transcript_log_path,
            request,
            result,
            sequence_index=sequence_index,
            robot_turn_index=robot_turn_index,
            robot_start_timestamp=robot_start_timestamp,
            robot_end_timestamp=robot_end_timestamp,
        )
        append_log_turn_block(
            base_payload.get("log_path"),
            request,
            result,
            participant_turns_for_reply,
            robot_end_timestamp,
        )
        phase_reply_count[current_phase] += 1
        if result_is_elicitation:
            last_elicitation_result = result
        pending_participant_turns = []

    def record_final_elicitation_evaluation():
        nonlocal sequence_index, last_elicitation_result, final_evaluation_recorded
        if final_evaluation_recorded or not evaluation_elicitation:
            return

        engagement_score = ""
        event_phase = current_phase
        event_strategy = ""
        event_prompt_id = ""
        if last_elicitation_result:
            engagement_score = prompt_previous_elicitation_engagement(
                last_elicitation_result,
                input_queue=evaluation_input_queue(),
                stop_event=None,
            )
            event_phase = last_elicitation_result.get("phase", current_phase)
            event_strategy = last_elicitation_result.get("strategy", "")
            event_prompt_id = last_elicitation_result.get("prompt_id", "")

        confidence_score = prompt_creative_confidence(
            "end",
            input_queue=evaluation_input_queue(),
            stop_event=None,
        )
        if engagement_score == "" and confidence_score == "":
            final_evaluation_recorded = True
            return

        now = timestamp_from_epoch()
        sequence_index += 1
        append_transcript_event(
            transcript_log_path,
            base_payload,
            {
                "sequence_index": sequence_index,
                "robot_turn_index": robot_turn_index,
                "event_type": "session_end_evaluation",
                "timestamp": now,
                "start_timestamp": now,
                "end_timestamp": now,
                "speaker": "Researcher",
                "text": "Session-end engagement and creative confidence scores",
                "phase": event_phase,
                "strategy": event_strategy,
                "prompt_id": event_prompt_id,
                "source": "manual_evaluation",
                "elicitation_engagement_score": engagement_score,
                "creative_confidence_score": confidence_score,
                "evaluation_moment": "end",
            },
        )
        last_elicitation_result = None
        final_evaluation_recorded = True

    def handle_command(line):
        nonlocal current_phase, elicitation_mode
        text = (line or "").strip()
        if not text:
            return True

        upper = text.upper()
        parts = text.split()
        command = parts[0].lower() if parts else ""
        value = " ".join(parts[1:]).strip()

        if text.lower() in {"quit", "exit", "stop"}:
            record_final_elicitation_evaluation()
            stop_event.set()
            return False

        if upper == "ROBOT":
            trigger_robot({"timestamp": timestamp_from_epoch()}, trigger_reason="manual")
            return True

        if upper in {"CHANGE", "SWITCH", "NEXT PHASE"}:
            current_phase = "convergence" if current_phase == "divergence" else "divergence"
            base_payload["phase"] = current_phase
            print(f"--- Switched to phase: {current_phase} ---")
            return True

        if upper in {"DIVERGENCE", "CONVERGENCE"}:
            current_phase = upper.lower()
            base_payload["phase"] = current_phase
            print(f"--- Phase set to: {current_phase} ---")
            return True

        if upper in {"PROACTIVE", "REACTIVE"}:
            base_payload.setdefault("mode_combo", {})["initiative"] = upper.lower()
            print(f"--- Initiative switched to: {upper.lower()} ---")
            return True

        if command in {"initiative", "style", "role", "elicitation"}:
            selected = value.lower()
            if not selected:
                print(f"Usage: {command.upper()} <value>")
                return True

            if command == "initiative":
                if selected not in {"off", "reactive", "proactive"}:
                    print("Initiative must be off, reactive, or proactive.")
                    return True
                base_payload.setdefault("mode_combo", {})["initiative"] = selected
                print(f"--- Initiative switched to: {selected} ---")
                return True

            if command == "style":
                if selected not in {"off", "passive", "assertive", "supportive"}:
                    print("Style must be off, passive, assertive, or supportive.")
                    return True
                base_payload.setdefault("mode_combo", {})["style"] = selected
                print(f"--- Style switched to: {selected} ---")
                return True

            if command == "role":
                if selected not in {"off", "facilitator", "solutionist"}:
                    print("Role must be off, facilitator, or solutionist.")
                    return True
                base_payload.setdefault("mode_combo", {})["role"] = selected
                print(f"--- Role switched to: {selected} ---")
                return True

            if command == "elicitation":
                if selected not in {"off", "scheduled", "perspective_shift", "constraint_reframing", "generative", "elaboration_evidence"}:
                    print("Elicitation must be off, scheduled, perspective_shift, generative, constraint_reframing, or elaboration_evidence.")
                    return True
                elicitation_mode = selected
                print(f"--- Elicitation switched to: {selected} ---")
                return True

        if command == "group" and value:
            base_payload["group_id"] = value.upper()
            refresh_strategy_sequences()
            print(f"--- Group set to: {base_payload['group_id']} ---")
            return True

        if command == "theme" and value:
            theme_id, theme_text = resolve_theme_text(value, "")
            base_payload["theme_id"] = theme_id
            base_payload["theme"] = theme_text
            refresh_strategy_sequences()
            print(f"--- Theme set to: {theme_id or theme_text} ---")
            return True

        print(f"Unrecognized live control: {text}")
        return True

    record_session_start_evaluation()
    if stop_event.is_set():
        return

    listener.start()
    try:
        while True:
            if stop_event.is_set():
                break

            try:
                while True:
                    if not handle_command(command_queue.get_nowait()):
                        break
            except queue.Empty:
                pass
            if stop_event.is_set():
                break

            event = listener.get_event(timeout=0.2)
            if event is None:
                maybe_trigger_proactive_silence()
                continue

            event_type = event.get("type")
            if event_type == "turn":
                turn = event["turn"]
                turn_text = turn.get("text", "")
                turn_epoch = epoch_from_timestamp(turn.get("timestamp")) or time.time()
                proactive_decision = evaluate_proactive_trigger(
                    turn_text,
                    trigger_words=trigger_words,
                    conversation_events=conversation_history,
                    last_trigger_epoch=last_proactive_epoch,
                    current_epoch=turn_epoch,
                )
                proactive_triggered = current_initiative() == "proactive" and bool(proactive_decision.get("triggered"))
                triggered = current_initiative() == "reactive" and text_contains_trigger_word(turn_text, trigger_words)
                conversation_history.append(turn)
                pending_participant_turns.append(turn)
                sequence_index += 1
                print(f"[{turn.get('timestamp', '')} {turn.get('speaker', 'Participant')}] {turn.get('text', '')}")
                append_participant_transcript_event(
                    transcript_log_path,
                    base_payload,
                    turn,
                    sequence_index=sequence_index,
                    triggered_robot=triggered or proactive_triggered,
                    trigger_words=trigger_words,
                    robot_turn_index=robot_turn_index + 1 if (triggered or proactive_triggered) else "",
                )
                silence_started_epoch = None
                if proactive_triggered or triggered:
                    trigger_robot(
                        turn,
                        trigger_reasons=proactive_decision.get("reasons", []) if proactive_triggered else ["trigger_word"],
                    )
                    if proactive_triggered:
                        last_proactive_epoch = turn_epoch
                continue

            if event_type == "empty":
                print(
                    f"[{event.get('end_timestamp', '')} {event.get('speaker', 'Participant')}] "
                    "No speech recognized."
                )
                maybe_trigger_proactive_silence(epoch_from_timestamp(event.get("end_timestamp")) or time.time())
                continue

            if event_type in {"warning", "error"}:
                message = event.get("message", "")
                print(f"[Audio {event_type}] {message}")
                sequence_index += 1
                append_transcript_event(
                    transcript_log_path,
                    base_payload,
                    {
                        "sequence_index": sequence_index,
                        "event_type": event_type,
                        "timestamp": event.get("timestamp", timestamp_from_epoch()),
                        "start_timestamp": event.get("start_timestamp", ""),
                        "end_timestamp": event.get("end_timestamp", ""),
                        "speaker": event.get("speaker", ""),
                        "text": message,
                        "source": "audio",
                    },
                )
    except KeyboardInterrupt:
        print("--- Continuous live dialog stopped ---")
    finally:
        listener.stop()


def participant_channel_map_from_args(args):
    return (
        (args.participant_1_channel, args.participant_1_name),
        (args.participant_2_channel, args.participant_2_name),
    )


def build_continuous_audio_transcriber_from_args(args, participant_channel_map):
    return ContinuousAudioTranscriber(
        api_key=args.deepgram_api_key,
        endpoint=args.deepgram_endpoint,
        input_mode=args.audio_input_mode,
        audio_device=args.audio_device,
        samplerate=args.audio_samplerate,
        participant_channel_map=participant_channel_map,
        vad_start_rms=args.vad_start_rms,
        vad_stop_rms=args.vad_stop_rms,
        vad_end_silence_seconds=args.vad_end_silence_seconds,
        vad_pre_roll_seconds=args.vad_pre_roll_seconds,
        vad_min_speech_seconds=args.vad_min_speech_seconds,
        vad_max_segment_seconds=args.vad_max_segment_seconds,
        blocksize=args.audio_blocksize,
        transcribe_workers=args.deepgram_workers,
    )


def resolve_theme_text(theme_id, custom_theme=""):
    custom_theme = (custom_theme or "").strip()
    theme_id = (theme_id or "").strip().upper()
    if custom_theme:
        return theme_id, custom_theme

    themes = load_themes()
    if theme_id and theme_id in themes:
        return theme_id, themes[theme_id].get("description", "")

    if theme_id:
        print(f"Warning: theme_id {theme_id} not found; using generic live theme.")
    return theme_id, "Open brainstorming conversation"


def safe_strategy_sequences(group_id, theme_id):
    sequences = {"divergence": [], "convergence": []}
    if not group_id or not theme_id:
        return sequences

    for phase in sequences:
        try:
            sequences[phase] = get_strategy_sequence(group_id, theme_id, phase)
        except Exception as error:
            print(f"Warning: counterbalancing unavailable for {group_id}/{theme_id}/{phase}: {error}")
            sequences[phase] = []
    return sequences


def is_elicitation_result(result):
    if not result or not result.get("prompt_id"):
        return False
    strategy = normalize_elicitation(str(result.get("strategy", "")).strip())
    return strategy in MODE_REGISTRY["elicitation"]


def parse_1_100_score(value):
    text = str(value or "").strip()
    if not text or text.lower() in {"skip", "na", "n/a", "none"}:
        return ""
    score = float(text)
    if score < 1 or score > 100:
        raise ValueError("score must be between 1 and 100")
    if score.is_integer():
        return int(score)
    return round(score, 2)


def parse_engagement_score(value):
    return parse_1_100_score(value)


def prompt_1_100_score(prompt, input_queue=None, stop_event=None):
    while True:
        if input_queue is None:
            try:
                raw = input(prompt)
            except EOFError:
                return ""
        else:
            print(prompt, end="", flush=True)
            while True:
                if stop_event is not None and stop_event.is_set():
                    print("")
                    return ""
                try:
                    raw = input_queue.get(timeout=0.1)
                    break
                except queue.Empty:
                    continue

        text = str(raw or "").strip()
        if text.lower() in LIVE_EXIT_WORDS:
            if stop_event is not None:
                stop_event.set()
            return ""
        try:
            return parse_1_100_score(text)
        except ValueError:
            print("Please enter a number from 1 to 100, or press Enter to skip.")


def prompt_start_elicitation_engagement(input_queue=None, stop_event=None):
    prompt = "[Evaluation] Engagement at start of conversation before first elicitation strategy, 1-100; Enter/skip to omit: "
    return prompt_1_100_score(prompt, input_queue=input_queue, stop_event=stop_event)


def prompt_creative_confidence(moment, input_queue=None, stop_event=None):
    moment_label = (moment or "").strip().lower()
    suffix = f" ({moment_label}, 1-100; Enter/skip to omit): " if moment_label else " (1-100; Enter/skip to omit): "
    prompt = f"[Evaluation] How confident are you in your creative abilities?{suffix}"
    return prompt_1_100_score(prompt, input_queue=input_queue, stop_event=stop_event)


def prompt_previous_elicitation_engagement(previous_result, input_queue=None, stop_event=None):
    if not previous_result:
        print("[Evaluation] First elicitation prompt; the first completed window can be scored at the next elicitation prompt.")
        return ""

    label = (
        f"{previous_result.get('phase', '')}/{previous_result.get('strategy', '')} "
        f"{previous_result.get('prompt_id', '')}"
    ).strip()
    prompt = f"[Evaluation] Engagement for previous elicitation window ({label}), 1-100; Enter/skip to omit: "
    return prompt_1_100_score(prompt, input_queue=input_queue, stop_event=stop_event)


def attach_previous_elicitation_engagement(result, previous_result, score):
    if not previous_result or score == "":
        return
    result["elicitation_engagement_score"] = score
    result["previous_elicitation_prompt_id"] = previous_result.get("prompt_id", "")
    result["previous_elicitation_strategy"] = previous_result.get("strategy", "")
    result["previous_elicitation_phase"] = previous_result.get("phase", "")


def start_live_command_reader(command_queue, stop_event):
    def reader():
        while not stop_event.is_set():
            try:
                line = input().strip()
            except EOFError:
                stop_event.set()
                break
            except Exception:
                time.sleep(0.1)
                continue
            if line:
                command_queue.put(line)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return thread


def print_live_control_summary(payload, elicitation_mode, intervention_every, keyboard_controls):
    mode_combo = payload.get("mode_combo", {})
    print(
        "Live controls: "
        f"group={payload.get('group_id', '')}, theme={payload.get('theme_id', '')}, "
        f"phase={payload.get('phase', '')}, elicitation={elicitation_mode}, "
        f"style={mode_combo.get('style', 'off')}, initiative={mode_combo.get('initiative', 'off')}, "
        f"role={mode_combo.get('role', 'off')}, every={intervention_every}"
    )
    if keyboard_controls:
        print(
            "Optional typed controls while mics run: ROBOT, CHANGE, DIVERGENCE, CONVERGENCE, "
            "ELICITATION off|scheduled|perspective_shift|generative|elaboration_evidence, "
            "STYLE off|passive|assertive|supportive, INITIATIVE off|reactive|proactive, "
            "ROLE off|facilitator|solutionist, GROUP G01, THEME T1, exit."
        )


def build_live_payload(args):
    theme_id, theme_text = resolve_theme_text(args.theme_id, args.theme)
    group_id = (args.group_id or "G01").strip().upper()
    chosen_initiative = args.initiative or "reactive"
    conversation_id = f"{group_id}_{theme_id or 'LIVE'}_{int(time.time())}"
    return {
        "server_url": "http://127.0.0.1:1234/v1/chat/completions",
        "model": "phi-3.5-mini-3.8b-instruct",
        "session_id": f"LIVE_{group_id}_{theme_id or int(time.time())}",
        "group_id": group_id,
        "conversation_id": conversation_id,
        "theme": theme_text,
        "theme_id": theme_id,
        "phase": args.phase,
        "log_path": DEFAULT_LOG_PATH,
        "transcript_log_path": args.transcript_log_path,
        "audio_input_mode": args.audio_input_mode if args.deepgram_live else "",
        "audio_device": args.audio_device or "",
        "mode_combo": {
            "elicitation": "perspective_shift",
            "style": args.style_mode,
            "initiative": chosen_initiative,
            "role": args.role_mode,
        },
        "seed_ideas": [],
        "conversation_history": [],
        "history_window_turns": 12,
        "temperature": 0.35,
        "max_tokens": 600,
        "reply_max_words": DEFAULT_REPLY_MAX_WORDS,
        "timeout_seconds": 30.0,
        "fallback_prompt": "Could you expand on that from a different angle?",
        "context_fallback": "What should we focus on next from what you just said?",
    }



def main():
    load_local_env_file()

    parser = argparse.ArgumentParser(description="Minimal LM Studio bridge for scripted elicitation prompts")
    parser.add_argument("--request", help="Path to a one-turn JSON request")
    parser.add_argument("--simulate", help="Path to a multi-turn session JSON file")
    parser.add_argument("--intervene", action="store_true", help="Manual intervention mode with counterbalancing schedule")
    parser.add_argument("--live", action="store_true", help="Live conversation mode (console or Pepper I/O)")
    parser.add_argument("--initiative", choices=["off", "reactive", "proactive"], help="Initiative mode for live/intervene (off|reactive|proactive)")
    parser.add_argument("--theme", default="", help="Custom theme text. If omitted, --theme-id is loaded from design/themes.json.")
    parser.add_argument("--theme-id", default="T1", help="Theme ID for live experiment mode, e.g. T1 or T2")
    parser.add_argument("--group-id", default="G01", help="Group ID for live experiment mode and counterbalancing, e.g. G01")
    parser.add_argument("--phase", choices=["divergence", "convergence"], default="divergence", help="Starting phase")
    parser.add_argument(
        "--elicitation-mode",
        choices=["off", "scheduled", "perspective_shift", "constraint_reframing", "generative", "elaboration_evidence"],
        default="scheduled",
        help="Live robot strategy mode: off, scheduled counterbalancing, or a fixed elicitation strategy.",
    )
    parser.add_argument("--intervention-every", type=int, default=4, help="Scheduled elicitation fires every Nth robot reply")
    parser.add_argument(
        "--evaluation_elicitation",
        "--evaluation-elicitation",
        action="store_true",
        dest="evaluation_elicitation",
        help="In live scheduled/fixed elicitation mode, ask the researcher for 1-100 start/end evaluation scores, including engagement and creative-confidence ratings.",
    )
    parser.add_argument("--style-mode", choices=["off", "passive", "assertive", "supportive"], default="assertive", help="Robot style guidance")
    parser.add_argument("--role-mode", choices=["off", "facilitator", "solutionist"], default="facilitator", help="Robot role guidance")
    parser.add_argument("--proactive-silence-threshold", type=float, default=PROACTIVE_SILENCE_THRESHOLD, help="Seconds of silence before proactive robot intervention")
    parser.add_argument("--no-live-keyboard-controls", action="store_true", help="Disable optional typed live controls while microphones run")
    parser.add_argument("--pepper", action="store_true", help="Use Pepper NAOqi I/O in live mode")
    parser.add_argument("--pepper-ip", default="169.254.166.52", help="Pepper robot IP")
    parser.add_argument("--pepper-port", type=int, default=9559, help="Pepper NAOqi port")
    parser.add_argument("--pepper-language", default="English", help="Pepper ASR language")
    parser.add_argument("--pepper-vocabulary", help="Comma-separated vocabulary for Pepper ASR")
    parser.add_argument("--asr-timeout", type=float, default=12.0, help="Seconds to wait for one Pepper ASR result")
    parser.add_argument("--asr-min-confidence", type=float, default=0.45, help="Minimum confidence for Pepper ASR result")
    parser.add_argument("--pepper-legacy-python", default=DEFAULT_PEPPER_LEGACY_PYTHON, help="Python launcher command for Python 2.7 Pepper helper")
    parser.add_argument("--pepper-legacy-tts-script", default=str(ROOT.parent / "pepper" / "tts.py"), help="Path to Python 2.7 Pepper TTS helper script")
    parser.add_argument(
        "--deepgram-api-key",
        default=get_env_secret(DEEPGRAM_API_KEY_ENV_VAR, DEFAULT_DEEPGRAM_API_KEY),
        help=f"Deepgram API key for speech-to-text audio transcription. Defaults to ${DEEPGRAM_API_KEY_ENV_VAR}.",
    )
    parser.add_argument("--deepgram-audio", help="Path to an audio file for Deepgram transcription")
    parser.add_argument("--deepgram-live", action="store_true", help="Use microphone + Deepgram for live participant speech recognition")
    parser.add_argument("--deepgram-record-seconds", type=float, default=20.0, help="Maximum seconds to record from the microphone for each live speech turn")
    parser.add_argument("--deepgram-endpoint", default="https://api.eu.deepgram.com/v1/listen", help="Deepgram STT endpoint URL")
    parser.add_argument("--transcript-log-path", default=DEFAULT_TRANSCRIPT_LOG_PATH, help="CSV transcript log path for live microphone runs")
    parser.add_argument("--list-audio-devices", action="store_true", help="List available microphone/input devices and exit")
    parser.add_argument(
        "--audio-capture-mode",
        choices=["continuous", "manual"],
        default=DEFAULT_AUDIO_CAPTURE_MODE,
        help="Continuous keeps microphones live and triggers on Pepper; manual records after Enter.",
    )
    parser.add_argument(
        "--audio-input-mode",
        choices=["focusrite", "laptop"],
        default=DEFAULT_AUDIO_INPUT_MODE,
        help="Live Deepgram microphone mode. Defaults to focusrite; use laptop for the built-in/default laptop microphone.",
    )
    parser.add_argument("--audio-device", help="Input device index or name substring. Use with --list-audio-devices if autodetect misses.")
    parser.add_argument("--audio-samplerate", type=int, help="Override the input sample rate. Defaults to the selected device's default rate.")
    parser.add_argument("--audio-silence-rms", type=float, default=120.0, help="Focusrite per-channel RMS below this is skipped as silence. Use 0 to transcribe every channel.")
    parser.add_argument("--participant-1-channel", type=int, default=1, help="Focusrite input channel mapped to Participant 1")
    parser.add_argument("--participant-2-channel", type=int, default=2, help="Focusrite input channel mapped to Participant 2")
    parser.add_argument("--participant-1-name", default="Participant 1", help="Speaker label for Focusrite participant 1")
    parser.add_argument("--participant-2-name", default="Participant 2", help="Speaker label for Focusrite participant 2")
    parser.add_argument("--trigger-words", default="pepper,paper", help="Comma-separated words that trigger the robot in continuous microphone mode")
    parser.add_argument("--vad-start-rms", type=float, default=400.0, help="Continuous mode RMS threshold that starts a speech segment")
    parser.add_argument("--vad-stop-rms", type=float, default=220.0, help="Continuous mode RMS threshold below which audio counts as silence")
    parser.add_argument("--vad-end-silence-seconds", type=float, default=0.8, help="Continuous mode silence duration that ends a speech segment")
    parser.add_argument("--vad-pre-roll-seconds", type=float, default=0.25, help="Continuous mode audio kept before speech starts")
    parser.add_argument("--vad-min-speech-seconds", type=float, default=0.35, help="Continuous mode minimum segment length to transcribe")
    parser.add_argument("--vad-max-segment-seconds", type=float, default=18.0, help="Continuous mode maximum segment length before forcing transcription")
    parser.add_argument("--audio-blocksize", type=int, default=1024, help="Continuous mode sounddevice block size")
    parser.add_argument("--deepgram-workers", type=int, default=2, help="Number of background Deepgram transcription workers")
    args = parser.parse_args()

    if args.list_audio_devices:
        print_audio_devices()
        return

    if not any([args.request, args.simulate, args.intervene, args.live, args.deepgram_audio, args.deepgram_live]):
        parser.error("Choose --request, --simulate, --intervene, --live, --deepgram-audio, or --deepgram-live")

    if args.deepgram_live and not (args.live or args.intervene):
        parser.error("--deepgram-live requires --live or --intervene")

    if args.deepgram_live and not args.deepgram_api_key:
        parser.error("--deepgram-api-key is required when using --deepgram-live")

    if args.deepgram_audio:
        if not args.deepgram_api_key:
            parser.error("--deepgram-api-key is required when using --deepgram-audio")

        transcript = deepgram_transcribe_file(
            audio_path=args.deepgram_audio,
            api_key=args.deepgram_api_key,
            endpoint=args.deepgram_endpoint,
        )
        print(f"[Deepgram] Recognized speech: {transcript}")

        if args.request:
            payload = load_json(pathlib.Path(args.request))
        else:
            payload = {
                "server_url": "http://127.0.0.1:1234/v1/chat/completions",
                "model": "phi-3.5-mini-3.8b-instruct",
                "session_id": f"AUDIO_{int(time.time())}",
                "group_id": "G_AUDIO",
                "conversation_id": f"AUDIO_{int(time.time())}",
                "theme": args.theme,
                "phase": "divergence",
                "log_path": DEFAULT_LOG_PATH,
                "mode_combo": {
                    "elicitation": "perspective_shift",
                    "style": MODE_DEFAULTS["style"],
                    "initiative": "reactive",
                    "role": "facilitator",
                },
                "seed_ideas": [],
                "conversation_history": [],
                "history_window_turns": 12,
                "temperature": 0.35,
                "max_tokens": 600,
                "reply_max_words": DEFAULT_REPLY_MAX_WORDS,
                "timeout_seconds": 30.0,
                "context_fallback": "What should we focus on next from what you just said?",
            }

        request = build_audio_turn_request(payload, transcript)
        result = process_context_only(request)
        print(json.dumps(result, ensure_ascii=True))
        return

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
        if args.deepgram_live and not args.deepgram_api_key:
            parser.error("--deepgram-api-key is required when using --deepgram-live")

        payload = build_live_payload(args)
        if args.evaluation_elicitation and not (args.deepgram_live and args.audio_capture_mode == "continuous"):
            print("Warning: --evaluation_elicitation currently applies to continuous live elicitation runs.")

        send_fn = console_send
        pepper = None
        if args.pepper:
            send_fn, pepper = build_pepper_tts_sender(args)
            print("Pepper TTS enabled for live mode.")

        try:
            if args.deepgram_live and args.audio_capture_mode == "continuous":
                participant_channel_map = participant_channel_map_from_args(args)
                listener = build_continuous_audio_transcriber_from_args(args, participant_channel_map)
                payload["audio_device"] = listener.device_name
                run_continuous_live_dialog(
                    payload,
                    listener,
                    send_fn,
                    trigger_words=parse_trigger_words(args.trigger_words),
                    elicitation_mode=args.elicitation_mode,
                    intervention_every=args.intervention_every,
                    keyboard_controls=not args.no_live_keyboard_controls,
                    proactive_silence_threshold=args.proactive_silence_threshold,
                    evaluation_elicitation=args.evaluation_elicitation,
                )
                return

            receive_fn = console_receive
            if args.deepgram_live:
                participant_channel_map = participant_channel_map_from_args(args)
                receive_fn = build_deepgram_live_receiver(
                    api_key=args.deepgram_api_key,
                    endpoint=args.deepgram_endpoint,
                    record_seconds=args.deepgram_record_seconds,
                    input_mode=args.audio_input_mode,
                    audio_device=args.audio_device,
                    samplerate=args.audio_samplerate,
                    silence_rms=args.audio_silence_rms,
                    participant_channel_map=participant_channel_map,
                )

            run_live_dialog(payload, receive_fn, send_fn)
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
            "transcript_log_path": args.transcript_log_path,
            "audio_input_mode": args.audio_input_mode if args.deepgram_live else "",
            "audio_device": args.audio_device or "",
            "mode_combo": {
                "elicitation": "perspective_shift",
                "style": args.style_mode,
                "initiative": chosen_initiative,
                "role": "facilitator",
            },
            "seed_ideas": [],
            "conversation_history": [],
            "history_window_turns": 12,
            "temperature": 0.35,
            "max_tokens": 600,
            "reply_max_words": DEFAULT_REPLY_MAX_WORDS,
            "timeout_seconds": 30.0,
            "fallback_prompt": "Could you expand on that from a different angle?",
            "context_fallback": "What should we focus on next from what you just said?",
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
        sequence_index = 0
        current_phase = "divergence"
        transcript_log_path = payload.get("transcript_log_path", DEFAULT_TRANSCRIPT_LOG_PATH)

        print("--- Intervene mode started ---")
        print("Commands: CHANGE (phase switch), ROBOT (robot intervention), PROACTIVE/REACTIVE (switch initiative), exit (stop)")

        say_from_intervene = None
        pepper_tts_client = None
        if args.pepper:
            say_from_intervene, pepper_tts_client = build_pepper_tts_sender(args)
            print("Pepper TTS enabled for intervene mode.")

        continuous_listener = None
        trigger_words = parse_trigger_words(args.trigger_words)
        if args.deepgram_live:
            if args.audio_capture_mode != "continuous":
                parser.error("--intervene --deepgram-live uses --audio-capture-mode continuous")
            participant_channel_map = participant_channel_map_from_args(args)
            continuous_listener = build_continuous_audio_transcriber_from_args(args, participant_channel_map)
            payload["audio_device"] = continuous_listener.device_name
            continuous_listener.start()
            print("Continuous microphone input enabled. Say Pepper or Paper to trigger the same action as the ROBOT command.")

        # Event queue and monitor thread for proactive silence detection
        event_queue = queue.Queue()
        input_queue = queue.Queue()
        stop_event = threading.Event()
        last_triggered_last_epoch = 0.0
        input_lock = threading.Lock()
        current_input_line = [""]  # list so it's mutable in nested scope

        def monitor_silence():
            nonlocal last_triggered_last_epoch
            silence_started_epoch = None
            while not stop_event.is_set():
                try:
                    initiative = payload.get("mode_combo", {}).get("initiative", "reactive")
                    if initiative == "proactive" and pending_participant_turns:
                        if continuous_listener and continuous_listener.has_active_speech:
                            silence_started_epoch = None
                            time.sleep(1)
                            continue

                        now_epoch = time.time()
                        if silence_started_epoch is None:
                            silence_started_epoch = now_epoch

                        elapsed = now_epoch - silence_started_epoch
                        if elapsed >= PROACTIVE_SILENCE_THRESHOLD and (now_epoch - last_triggered_last_epoch) >= PROACTIVE_TRIGGER_COOLDOWN_SECONDS:
                            event_queue.put("AUTO_ROBOT")
                            last_triggered_last_epoch = now_epoch
                            silence_started_epoch = None
                    else:
                        silence_started_epoch = None
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
        def trigger_robot(trigger_reason=None, trigger_reasons=None):
            nonlocal turn_index, pending_participant_turns, sequence_index
            if not pending_participant_turns:
                print("Robot: No participant turns buffered yet.")
                return

            turn_index += 1
            participant_timestamp = pending_participant_turns[0]["timestamp"]
            recent_turn_lines = [f"{item['speaker']}: {item['text']}" for item in pending_participant_turns]
            participant_log_text = " || ".join(recent_turn_lines)

            payload["phase"] = current_phase
            payload["turn_index"] = turn_index
            payload["turn_timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            payload["last_user_utterance"] = format_participant_turns_text(pending_participant_turns)
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
            print_robot_trigger_reason(trigger_reason=trigger_reason, trigger_reasons=trigger_reasons)
            print_robot_turn(result)
            sequence_index += 1
            append_robot_transcript_event(
                transcript_log_path,
                payload,
                result,
                sequence_index=sequence_index,
                robot_turn_index=turn_index,
                robot_timestamp=robot_timestamp,
            )
            if say_from_intervene:
                try:
                    style = result.get("style", MODE_DEFAULTS["style"])
                    say_from_intervene(result["reply"], style=style)
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

        def handle_audio_event(event):
            nonlocal sequence_index
            event_type = event.get("type")

            if event_type == "turn":
                turn = event["turn"]
                turn_text = turn.get("text", "")
                triggered = text_contains_trigger_word(turn_text, trigger_words)
                turn_epoch = epoch_from_timestamp(turn.get("timestamp")) or time.time()
                proactive_decision = evaluate_proactive_trigger(
                    turn_text,
                    trigger_words=trigger_words,
                    conversation_events=payload.get("conversation_history", []),
                    last_trigger_epoch=last_triggered_last_epoch,
                    current_epoch=turn_epoch,
                )
                proactive_triggered = payload.get("mode_combo", {}).get("initiative", MODE_DEFAULTS["initiative"]) == "proactive" and bool(proactive_decision.get("triggered"))
                payload["phase"] = current_phase
                payload.setdefault("conversation_history", []).append(turn)
                pending_participant_turns.append(turn)
                sequence_index += 1
                print(f"[{turn.get('timestamp', '')} {turn.get('speaker', 'Participant')}] {turn.get('text', '')}")
                append_participant_transcript_event(
                    transcript_log_path,
                    payload,
                    turn,
                    sequence_index=sequence_index,
                    triggered_robot=triggered or proactive_triggered,
                    trigger_words=trigger_words,
                    robot_turn_index=turn_index + 1 if (triggered or proactive_triggered) else "",
                )
                if proactive_triggered or triggered:
                    trigger_robot(
                        trigger_reasons=proactive_decision.get("reasons", []) if proactive_triggered else ["trigger_word"],
                    )
                    if proactive_triggered:
                        last_triggered_last_epoch = turn_epoch
                return

            if event_type == "empty":
                print(
                    f"[{event.get('end_timestamp', '')} {event.get('speaker', 'Participant')}] "
                    "No speech recognized."
                )
                return

            if event_type in {"warning", "error"}:
                message = event.get("message", "")
                print(f"[Audio {event_type}] {message}")
                sequence_index += 1
                append_transcript_event(
                    transcript_log_path,
                    payload,
                    {
                        "sequence_index": sequence_index,
                        "event_type": event_type,
                        "timestamp": event.get("timestamp", timestamp_from_epoch()),
                        "start_timestamp": event.get("start_timestamp", ""),
                        "end_timestamp": event.get("end_timestamp", ""),
                        "speaker": event.get("speaker", ""),
                        "text": message,
                        "source": "audio",
                    },
                )

        try:
            while True:
                if continuous_listener:
                    audio_event = continuous_listener.get_event(timeout=0.02)
                    if audio_event is not None:
                        handle_audio_event(audio_event)
                        continue

            # Handle any queued auto-trigger events first
                try:
                    ev = event_queue.get_nowait()
                except queue.Empty:
                    ev = None

                if ev == "AUTO_ROBOT":
                # Auto-trigger from proactive monitor
                    trigger_robot(trigger_reason="silence")
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
                    trigger_robot(trigger_reason="manual")
                    continue

                participant_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
                speaker, text = parse_speaker_prefixed_text(line)
                participant_turn = make_participant_turn(speaker, text, timestamp=participant_timestamp)
                triggered = text_contains_trigger_word(text, trigger_words)
                proactive_triggered = payload.get("mode_combo", {}).get("initiative", MODE_DEFAULTS["initiative"]) == "proactive" and should_trigger_proactive_robot(
                    text,
                    trigger_words=trigger_words,
                )

                payload.setdefault("conversation_history", []).append(participant_turn)
                pending_participant_turns.append(participant_turn)
                sequence_index += 1
                append_participant_transcript_event(
                    transcript_log_path,
                    payload,
                    participant_turn,
                    sequence_index=sequence_index,
                    triggered_robot=triggered,
                    trigger_words=trigger_words,
                    robot_turn_index=turn_index + 1 if triggered else "",
                )

                # Reactive immediate trigger: if initiative is reactive and the participant called the robot's name, intervene now
                try:
                    initiative_now = payload.get("mode_combo", {}).get("initiative", MODE_DEFAULTS["initiative"])
                    if initiative_now == "proactive" and proactive_triggered:
                        proactive_decision = evaluate_proactive_trigger(
                            text,
                            trigger_words=trigger_words,
                            conversation_events=payload.get("conversation_history", []),
                        )
                        trigger_robot(trigger_reasons=proactive_decision.get("reasons", []))
                        continue
                    if initiative_now == "reactive" and triggered:
                        trigger_robot(trigger_reasons=["trigger_word"])
                        continue
                except Exception:
                    pass
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            stop_event.set()
            if continuous_listener:
                continuous_listener.stop()
            monitor_thread.join(timeout=1)
            if pepper_tts_client:
                pepper_tts_client.close()


if __name__ == "__main__":
    main()