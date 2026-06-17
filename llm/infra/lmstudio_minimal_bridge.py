#!/usr/bin/env python3
import argparse
import csv
import io
import json
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
BUNDLED_PY27 = ROOT.parent / ".tools" / "Python27" / "python.exe"
DEFAULT_LOG_PATH = "logs/logs.csv"
PROACTIVE_SILENCE_THRESHOLD = 10  # seconds of silence to trigger proactive intervention
ASR_MEMORY_KEY = "WordRecognized"
LIVE_EXIT_WORDS = {"quit", "exit", "stop", "stop conversation"}

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


def default_py27_command():
    if BUNDLED_PY27.exists():
        return str(BUNDLED_PY27)
    return "py -2.7"

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
    "style": "passive",
    "initiative": "reactive",
    "role": "facilitator",
}

STYLE_SETUP = {
    "passive": "Use a gentle, low-pressure, non-directive tone.",
    "assertive": "Use a concise and direct facilitation tone while staying neutral.",
    "supportive": "Use a warm, encouraging, and empathetic tone.",
}

VOCAL_DELIVERY = {
    "passive": {
        "speed": 110.0,        # Project default speech rate percent
        "volume": 0.8,
        "pitch": 1.0,
        "pause_ms": 220,
    },
    "assertive": {
        "speed": 110.0,
        "volume": 0.8,
        "pitch": 1.0,
        "pause_ms": 220,
    },
    "supportive": {
        "speed": 110.0,
        "volume": 0.8,
        "pitch": 1.0,
        "pause_ms": 220,
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
    "Keep responses brief (1-3 sentences) and focused on one key idea or question."
)


def normalize_tts_speed(value):
    try:
        speed = float(value)
    except Exception:
        return 110.0
    if speed <= 3.0:
        speed *= 100.0
    return max(50.0, min(140.0, speed))


def clamp_float(value, minimum, maximum, fallback):
    try:
        number = float(value)
    except Exception:
        return fallback
    return max(minimum, min(maximum, number))


def bool_from_value(value, fallback=True):
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return fallback


def soften_tts_text(text, pause_ms=220):
    if not text or not pause_ms:
        return text
    try:
        pause_value = int(pause_ms)
    except Exception:
        pause_value = 220
    if pause_value <= 0:
        return text
    pause = "\\pau=%d\\" % pause_value
    cleaned = " ".join(str(text).split())
    return re.sub(r"([.!?:])\s+", lambda match: match.group(1) + " " + pause + " ", cleaned)


def tts_overrides_from_args(args):
    overrides = {}
    for attr, key in [
        ("tts_speed", "speed"),
        ("tts_volume", "volume"),
        ("tts_pitch", "pitch"),
        ("tts_pause_ms", "pause_ms"),
        ("tts_voice", "voice"),
        ("look_at_people", "look_at_people"),
    ]:
        value = getattr(args, attr, None)
        if value not in (None, ""):
            overrides[key] = value
    return overrides


def resolve_vocal_delivery(style="passive", overrides=None):
    params = dict(VOCAL_DELIVERY.get(style) or VOCAL_DELIVERY["passive"])
    if overrides:
        params.update(overrides)
    params["speed"] = normalize_tts_speed(params.get("speed", 110.0))
    params["volume"] = clamp_float(params.get("volume", 1.0), 0.0, 1.0, 1.0)
    params["pitch"] = clamp_float(params.get("pitch", 1.0), 0.7, 1.3, 1.0)
    try:
        params["pause_ms"] = int(params.get("pause_ms", 220))
    except Exception:
        params["pause_ms"] = 280
    params["look_at_people"] = bool_from_value(params.get("look_at_people"), True)
    return params


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


def parse_audio_input_device(device):
    if device in (None, ""):
        return None
    try:
        return int(device)
    except Exception:
        return device


def resolve_audio_samplerate(input_device=None, samplerate=None):
    if samplerate not in (None, "", 0, "0"):
        try:
            return int(float(samplerate))
        except Exception:
            pass
    try:
        import sounddevice as sd
        device = parse_audio_input_device(input_device)
        if device is None:
            device = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
        info = sd.query_devices(device, "input")
        default_rate = int(round(float(info.get("default_samplerate") or 48000)))
        print(f"[Audio] Using input sample rate {default_rate} Hz.")
        return default_rate
    except Exception:
        return 48000


def audio_stats(recording):
    try:
        peak = int(abs(recording).max())
        rms = float(((recording.astype("float32") ** 2).mean()) ** 0.5)
        return {
            "peak": peak,
            "rms": rms,
            "peak_percent": round((peak / 32767.0) * 100.0, 1),
            "rms_percent": round((rms / 32767.0) * 100.0, 1),
        }
    except Exception:
        return {"peak": 0, "rms": 0.0, "peak_percent": 0.0, "rms_percent": 0.0}


def wav_bytes_from_recording(recording, samplerate, channels):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(recording.tobytes())
    return buffer.getvalue()


def parse_channel_names(value, count=2):
    if isinstance(value, str):
        names = [item.strip() for item in re.split(r"[,;]+", value) if item.strip()]
    elif value:
        names = [str(item).strip() for item in value if str(item).strip()]
    else:
        names = []
    while len(names) < count:
        names.append(f"Participant {len(names) + 1}")
    return names[:count]


def resolve_audio_separate_channels(separate_channels, channels):
    try:
        channel_count = int(channels or 1)
    except Exception:
        channel_count = 1
    if separate_channels is None:
        return channel_count >= 2
    if isinstance(separate_channels, str):
        value = separate_channels.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return bool(separate_channels)


def record_audio_from_mic(duration=6.0, samplerate=None, channels=1, input_device=None, include_stats=False, include_channel_audio=False):
    try:
        import sounddevice as sd
    except Exception as error:
        raise RuntimeError(
            "sounddevice is required for live microphone capture. Install it with `pip install sounddevice`."
        ) from error

    device = parse_audio_input_device(input_device)
    samplerate = resolve_audio_samplerate(device, samplerate)
    if device is not None:
        try:
            selected = sd.query_devices(device, "input")
            print(f"Recording microphone audio for up to {duration:.1f} seconds from {selected.get('name', device)}...")
        except Exception:
            print(f"Recording microphone audio for up to {duration:.1f} seconds from device {device}...")
    else:
        print(f"Recording microphone audio for up to {duration:.1f} seconds from the default input...")
    recording = sd.rec(
        int(duration * samplerate),
        samplerate=samplerate,
        channels=channels,
        dtype="int16",
        device=device,
    )
    sd.wait()
    stats = audio_stats(recording)
    audio_bytes = wav_bytes_from_recording(recording, samplerate, channels)
    channel_audio = []
    if include_channel_audio:
        for idx in range(channels):
            channel_recording = recording[:, idx:idx + 1]
            channel_audio.append({
                "index": idx,
                "audio": wav_bytes_from_recording(channel_recording, samplerate, 1),
                "stats": audio_stats(channel_recording),
            })
    if include_stats:
        if include_channel_audio:
            return audio_bytes, samplerate, channels, stats, channel_audio
        return audio_bytes, samplerate, channels, stats
    if include_channel_audio:
        return audio_bytes, samplerate, channels, channel_audio
    return audio_bytes, samplerate, channels


def record_audio_until_silence(
    max_seconds=10.0,
    samplerate=None,
    channels=1,
    input_device=None,
    silence_seconds=1.6,
    idle_seconds=1.0,
    min_record_seconds=0.8,
    speech_peak_threshold=180,
    frame_seconds=0.2,
    on_speech_start=None,
    on_speech_end=None,
    include_stats=False,
    include_channel_audio=False,
):
    try:
        import sounddevice as sd
        import numpy as np
    except Exception as error:
        raise RuntimeError(
            "sounddevice and numpy are required for live microphone capture. Install them with `pip install sounddevice numpy`."
        ) from error

    device = parse_audio_input_device(input_device)
    samplerate = resolve_audio_samplerate(device, samplerate)
    if device is not None:
        try:
            selected = sd.query_devices(device, "input")
            print(f"Listening from {selected.get('name', device)}; waiting for speech...")
        except Exception:
            print(f"Listening from device {device}; waiting for speech...")
    else:
        print("Listening from the default input; waiting for speech...")

    blocksize = max(256, int(float(frame_seconds) * samplerate))
    max_seconds = max(1.0, float(max_seconds))
    silence_seconds = max(0.2, float(silence_seconds))
    idle_seconds = max(0.2, float(idle_seconds))
    min_record_seconds = max(0.0, float(min_record_seconds))
    threshold = max(1, int(speech_peak_threshold))

    frames = []
    speech_started = False
    speech_start = None
    silence_started = None
    listen_start = time.monotonic()

    with sd.InputStream(samplerate=samplerate, channels=channels, dtype="int16", device=device, blocksize=blocksize) as stream:
        while True:
            now = time.monotonic()
            if not speech_started and now - listen_start >= idle_seconds:
                break
            if speech_started and now - speech_start >= max_seconds:
                break

            data, _overflowed = stream.read(blocksize)
            peak = int(abs(data).max()) if data.size else 0
            has_speech = peak >= threshold

            if has_speech and not speech_started:
                speech_started = True
                speech_start = now
                silence_started = None
                print("[Audio] speech started")
                if on_speech_start:
                    try:
                        on_speech_start()
                    except Exception:
                        pass

            if speech_started:
                frames.append(data.copy())
                if has_speech:
                    silence_started = None
                else:
                    if silence_started is None:
                        silence_started = now
                    if (now - speech_start) >= min_record_seconds and (now - silence_started) >= silence_seconds:
                        break

    if frames:
        recording = np.concatenate(frames, axis=0)
    else:
        recording = np.zeros((0, channels), dtype="int16")

    if speech_started and on_speech_end:
        try:
            on_speech_end()
        except Exception:
            pass

    stats = audio_stats(recording)
    audio_bytes = wav_bytes_from_recording(recording, samplerate, channels)
    channel_audio = []
    if include_channel_audio:
        for idx in range(channels):
            channel_recording = recording[:, idx:idx + 1]
            channel_audio.append({
                "index": idx,
                "audio": wav_bytes_from_recording(channel_recording, samplerate, 1),
                "stats": audio_stats(channel_recording),
            })
    if include_stats:
        if include_channel_audio:
            return audio_bytes, samplerate, channels, stats, channel_audio
        return audio_bytes, samplerate, channels, stats
    if include_channel_audio:
        return audio_bytes, samplerate, channels, channel_audio
    return audio_bytes, samplerate, channels


def build_deepgram_live_receiver(
    api_key,
    endpoint,
    record_seconds=6.0,
    input_device=None,
    channels=1,
    sample_rate=None,
    wait_for_enter=False,
    separate_channels=None,
    channel_names=None,
    channel_min_peak=250,
    channel_relative_peak=0.25,
    channel_relative_rms=0.20,
    endpointing=True,
    endpoint_silence_seconds=1.6,
    endpoint_idle_seconds=1.0,
    speech_peak_threshold=180,
    on_speech_start=None,
    on_speech_end=None,
):
    announced = [False]
    separate_channels = resolve_audio_separate_channels(separate_channels, channels)
    input_channels = max(2, int(channels or 1)) if separate_channels else int(channels or 1)
    speaker_names = parse_channel_names(channel_names, count=input_channels)

    def receive():
        if wait_for_enter:
            input("Press Enter to record your next response, then speak clearly: ")
        elif not announced[0]:
            print("[Deepgram] Continuous listening is on. Speak naturally; audio is processed in short chunks.")
            if separate_channels:
                print(f"[Deepgram] Separate channel mode: channel 1 = {speaker_names[0]}, channel 2 = {speaker_names[1]}")
            announced[0] = True
        if endpointing and not wait_for_enter:
            result = record_audio_until_silence(
                max_seconds=record_seconds,
                input_device=input_device,
                channels=input_channels,
                samplerate=sample_rate,
                silence_seconds=endpoint_silence_seconds,
                idle_seconds=endpoint_idle_seconds,
                speech_peak_threshold=speech_peak_threshold,
                on_speech_start=on_speech_start,
                on_speech_end=on_speech_end,
                include_stats=True,
                include_channel_audio=separate_channels,
            )
        else:
            result = record_audio_from_mic(
                duration=record_seconds,
                input_device=input_device,
                channels=input_channels,
                samplerate=sample_rate,
                include_stats=True,
                include_channel_audio=separate_channels,
            )
        if separate_channels:
            audio_bytes, samplerate, recorded_channels, stats, channel_audio = result
        else:
            audio_bytes, samplerate, recorded_channels, stats = result
        print(
            "[Audio] level peak={peak_percent}% rms={rms_percent}% channels={channels}".format(
                peak_percent=stats.get("peak_percent", 0.0),
                rms_percent=stats.get("rms_percent", 0.0),
                channels=recorded_channels,
            )
        )
        if stats.get("peak", 0) < 250:
            print("[Audio] Very low input level. Check Focusrite gain, input, cable, and phantom power if needed.")
        if endpointing and stats.get("peak", 0) < int(speech_peak_threshold):
            return [] if separate_channels else ""

        if separate_channels:
            lines = []
            channel_stats_list = [channel["stats"] for channel in channel_audio[:2]]
            max_channel_peak = max([stats.get("peak", 0) for stats in channel_stats_list] or [0])
            max_channel_rms = max([stats.get("rms", 0.0) for stats in channel_stats_list] or [0.0])
            for channel in channel_audio[:2]:
                idx = channel["index"]
                speaker = speaker_names[idx]
                channel_stats = channel["stats"]
                print(
                    "[Audio] {speaker} level peak={peak_percent}% rms={rms_percent}%".format(
                        speaker=speaker,
                        peak_percent=channel_stats.get("peak_percent", 0.0),
                        rms_percent=channel_stats.get("rms_percent", 0.0),
                    )
                )
                peak = channel_stats.get("peak", 0)
                rms = channel_stats.get("rms", 0.0)
                relative_peak_floor = max_channel_peak * max(0.0, float(channel_relative_peak or 0.0))
                relative_rms_floor = max_channel_rms * max(0.0, float(channel_relative_rms or 0.0))
                if peak < int(channel_min_peak):
                    print(f"[Deepgram] {speaker}: <skipped, very low channel level>")
                    continue
                if max_channel_peak > 0 and peak < relative_peak_floor and rms < relative_rms_floor:
                    print(f"[Deepgram] {speaker}: <skipped, likely bleed from the other microphone>")
                    continue
                print(f"Sending {speaker} audio to Deepgram...")
                transcript = deepgram_transcribe_bytes(
                    audio_data=channel["audio"],
                    content_type="audio/wav",
                    api_key=api_key,
                    endpoint=endpoint,
                )
                if transcript:
                    print(f"[Deepgram] {speaker}: {transcript}")
                    lines.append(f"{speaker}: {transcript}")
                else:
                    print(f"[Deepgram] {speaker}: <empty>")
            return lines

        print("Sending audio to Deepgram for transcription...")
        content_type = "audio/wav"
        transcript = deepgram_transcribe_bytes(
            audio_data=audio_bytes,
            content_type=content_type,
            api_key=api_key,
            endpoint=endpoint,
        )
        if transcript:
            print(f"[Deepgram] Transcript: {transcript}")
        else:
            print("[Deepgram] Transcript: <empty>")
        return transcript

    return receive


def build_audio_turn_request(payload, transcript):
    participant_turn = {
        "speaker": "Participant",
        "text": transcript,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    request = dict(payload)
    request.update({
        "turn_index": request.get("turn_index", 1),
        "turn_timestamp": participant_turn["timestamp"],
        "last_user_utterance": transcript,
        "recent_participant_turns": [participant_turn],
        "conversation_history": list(request.get("conversation_history", [])) + [participant_turn],
    })
    return request


class PepperIO:
    def __init__(self, ip, port=9559, language="English", vocabulary=None, vocal_overrides=None):
        self.ip = ip
        self.port = int(port)
        self.language = language
        self.vocabulary = vocabulary or []
        self.vocal_overrides = vocal_overrides or {}
        self.tts = None
        self.asr = None
        self.memory = None
        self.awareness = None
        self.motion = None
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
        try:
            self.awareness = ALProxy("ALBasicAwareness", self.ip, self.port)
        except Exception:
            self.awareness = None
        try:
            self.motion = ALProxy("ALMotion", self.ip, self.port)
        except Exception:
            self.motion = None
        try:
            self.tts.setLanguage(self.language)
        except Exception:
            pass
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

    def set_vocal_params(self, speed=110.0, volume=0.8, pitch=1.0, voice=None, **_ignored):
        """Set vocal delivery parameters for speech.
        
        Args:
            speed: Speech rate percent (100 is default; 80-90 is calmer)
            volume: Volume level (0.0-1.0, default 0.8)
            pitch: Pitch shift (0.7-1.3, default 1.0)
        """
        if not self.tts:
            return
        try:
            if voice:
                self.tts.setVoice(voice)
        except Exception:
            pass
        try:
            self.tts.setParameter("speed", normalize_tts_speed(speed))
        except Exception:
            pass
        for name in ("doubleVoice", "doubleVoiceLevel", "doubleVoiceTimeShift"):
            try:
                self.tts.setParameter(name, 0.0)
            except Exception:
                pass
        try:
            self.tts.setVolume(clamp_float(volume, 0.0, 1.0, 1.0))
        except Exception:
            pass
        try:
            self.tts.setParameter("pitchShift", clamp_float(pitch, 0.7, 1.3, 1.0))
        except Exception:
            try:
                self.tts.setParameter("pitch", clamp_float(pitch, 0.7, 1.3, 1.0))
            except Exception:
                pass

    def reset_vocal_params(self):
        if not self.tts:
            return
        try:
            self.tts.setParameter("speed", 100.0)
        except Exception:
            pass
        try:
            self.tts.setVolume(1.0)
        except Exception:
            pass
        try:
            self.tts.setParameter("pitchShift", 1.0)
        except Exception:
            pass

    def look_at_people(self):
        if self.motion:
            try:
                self.motion.wakeUp()
            except Exception:
                pass
            try:
                self.motion.setStiffnesses("Head", 1.0)
            except Exception:
                pass
            try:
                self.motion.angleInterpolationWithSpeed(["HeadYaw", "HeadPitch"], [0.0, -0.05], 0.35)
            except Exception:
                pass
            return

        if self.awareness:
            for call in [
                lambda: self.awareness.setStimulusDetectionEnabled("People", True),
                lambda: self.awareness.setTrackingMode("Head"),
                lambda: self.awareness.setEngagementMode("SemiEngaged"),
                lambda: self.awareness.startAwareness(),
            ]:
                try:
                    call()
                except Exception:
                    pass

    def say(self, text, style="passive"):
        if not text:
            return
        if self.tts:
            params = resolve_vocal_delivery(style, self.vocal_overrides)
            self.set_vocal_params(**params)
            if params.get("look_at_people", True):
                self.look_at_people()
            self.tts.say(soften_tts_text(text, params.get("pause_ms", 220)))
            self.reset_vocal_params()

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


def send_to_pepper_via_py27(text, ip, port, script_path, python_cmd, vocal_params=None, language="English"):
    if not text:
        return

    vocal_params = vocal_params or {}
    command = list(python_cmd) + [
        str(script_path),
        "--ip",
        str(ip),
        "--port",
        str(port),
        "--language",
        str(language or "English"),
        "--say",
        text,
    ]
    for key, option in [
        ("speed", "--speed"),
        ("volume", "--volume"),
        ("pitch", "--pitch"),
        ("pause_ms", "--pause-ms"),
        ("voice", "--voice"),
    ]:
        value = vocal_params.get(key)
        if value not in (None, ""):
            command.extend([option, str(value)])
    if bool_from_value(vocal_params.get("look_at_people"), True):
        command.append("--look-at-people")
    else:
        command.append("--no-look-at-people")
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr_text = (completed.stderr or "").strip()
        stdout_text = (completed.stdout or "").strip()
        details = stderr_text or stdout_text or "unknown error"
        raise RuntimeError(f"Python 2.7 Pepper TTS bridge failed: {details}")


def build_pepper_tts_sender(args):
    vocal_overrides = tts_overrides_from_args(args)
    if ALProxy is None:
        legacy_python_cmd = args.pepper_legacy_python.strip().split()
        legacy_script = pathlib.Path(args.pepper_legacy_tts_script)
        if not legacy_script.is_absolute():
            legacy_script = (ROOT.parent / legacy_script).resolve()

        if not legacy_script.exists():
            raise RuntimeError(f"Legacy TTS script not found: {legacy_script}")

        print("naoqi SDK unavailable in Python 3; using Python 2.7 Pepper TTS bridge.")

        def sender(text, style="passive"):
            vocal_params = resolve_vocal_delivery(style, vocal_overrides)
            send_to_pepper_via_py27(
                text=text,
                ip=args.pepper_ip,
                port=args.pepper_port,
                script_path=legacy_script,
                python_cmd=legacy_python_cmd,
                vocal_params=vocal_params,
                language=args.pepper_language,
            )

        return sender, None

    pepper = PepperIO(
        ip=args.pepper_ip,
        port=args.pepper_port,
        language=args.pepper_language,
        vocabulary=parse_phrase_list(args.pepper_vocabulary) or DEFAULT_PEPPER_VOCABULARY,
        vocal_overrides=vocal_overrides,
    )
    pepper.connect()
    print(f"Connected to Pepper at {args.pepper_ip}:{args.pepper_port}")

    def sender(text, style="passive"):
        pepper.say(text, style=style)

    return sender, pepper


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
    role_key = get_mode_value(payload, "role")
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
        f"Initiative guidance: {INITIATIVE_SETUP[initiative_key]} "
        f"Role guidance: {ROLE_SETUP[role_key]}"
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
    role_key = get_mode_value(payload, "role")
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
        f"Role guidance: {ROLE_SETUP[role_key]} "
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
    }

    # Optional experimental thinking support.
    # Do not send these fields to LM Studio/Phi unless explicitly enabled.
    if payload.get("enable_thinking") is True:
        body["thinking"] = True
        body["enable_thinking"] = True

    request = urllib.request.Request(
        payload.get("server_url", "http://127.0.0.1:1234/v1/chat/completions"),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    timeout = float(payload.get("timeout_seconds", 30.0))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = ""
        try:
            details = error.read().decode("utf-8", errors="replace").strip()
        except Exception:
            details = ""
        if details:
            raise RuntimeError(f"HTTP {error.code}: {details}") from error
        raise RuntimeError(f"HTTP {error.code}: {error.reason}") from error

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

    style = get_mode_value(payload, "style")
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

    style = get_mode_value(payload, "style")
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
    print(f"[Robot:{result['source']}/{result.get('prompt_id', '')}] {result['reply']}")
    if result.get('source') == 'fallback' and result.get('fallback_reason'):
        print(f"[Fallback reason] {result['fallback_reason']}")


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


def console_send(text, style="passive"):
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
        received = receive_fn()
        participant_items = received if isinstance(received, (list, tuple)) else [received]
        participant_items = [str(item).strip() for item in participant_items if str(item or "").strip()]
        if not participant_items:
            continue

        for participant_text in participant_items:
            speaker = "Participant"
            if ":" in participant_text:
                left, right = participant_text.split(":", 1)
                if left.strip() and right.strip():
                    speaker = left.strip()
                    participant_text = right.strip()

            if participant_text.lower() in LIVE_EXIT_WORDS:
                print("--- Live dialog stopped ---")
                return

            participant_timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            participant_turn = {
                "speaker": speaker,
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
            send_fn(result["reply"], style=result.get("style", "passive"))
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
    parser.add_argument("--tts-speed", type=float, default=None, help="Pepper speech speed as percent; project default is 110")
    parser.add_argument("--tts-volume", type=float, default=None, help="Pepper speech volume from 0.0 to 1.0; project default is 0.8")
    parser.add_argument("--tts-pitch", type=float, default=None, help="Pepper pitch shift; project default is 1.0")
    parser.add_argument("--tts-pause-ms", type=int, default=None, help="Pause inserted after sentence punctuation in Pepper speech")
    parser.add_argument("--tts-voice", default=None, help="Optional exact Pepper voice name")
    parser.add_argument("--look-at-people", action=argparse.BooleanOptionalAction, default=True, help="Have Pepper look toward/track people before speaking")
    parser.add_argument("--asr-timeout", type=float, default=12.0, help="Seconds to wait for one Pepper ASR result")
    parser.add_argument("--asr-min-confidence", type=float, default=0.45, help="Minimum confidence for Pepper ASR result")
    parser.add_argument("--pepper-legacy-python", default=default_py27_command(), help="Python launcher command for Python 2.7 Pepper helper")
    parser.add_argument("--pepper-legacy-tts-script", default=str(ROOT.parent / "pepper" / "tts.py"), help="Path to Python 2.7 Pepper TTS helper script")
    parser.add_argument("--deepgram-api-key", help="Deepgram API key for speech-to-text audio transcription")
    parser.add_argument("--deepgram-audio", help="Path to an audio file for Deepgram transcription")
    parser.add_argument("--deepgram-live", action="store_true", help="Use microphone + Deepgram for live participant speech recognition")
    parser.add_argument("--deepgram-record-seconds", type=float, default=10.0, help="Maximum utterance length in endpointing mode; fixed chunk length when --no-audio-endpointing is used")
    parser.add_argument("--deepgram-endpoint", default="https://api.eu.deepgram.com/v1/listen", help="Deepgram STT endpoint URL")
    parser.add_argument("--deepgram-press-enter", action="store_true", help="Require Enter before each Deepgram recording chunk; continuous listening is the default")
    parser.add_argument("--audio-input-device", help="Optional sounddevice input device index or name, e.g. 2 for Focusrite Analogue 1+2")
    parser.add_argument("--audio-input-channels", type=int, default=1, help="Input channels to record; use 2 for two Focusrite microphone inputs")
    parser.add_argument("--audio-sample-rate", type=int, default=0, help="Input sample rate; 0 uses the selected device default, useful for Focusrite devices")
    parser.add_argument("--audio-separate-channels", action=argparse.BooleanOptionalAction, default=None, help="Split audio channels 1 and 2, transcribe separately, and label speakers; auto-enabled when --audio-input-channels is 2 or more")
    parser.add_argument("--audio-channel-names", default="Participant 1,Participant 2", help="Comma-separated names for separate channel mode")
    parser.add_argument("--audio-channel-min-peak", type=int, default=250, help="Skip a separated channel if its peak level is below this raw PCM value")
    parser.add_argument("--audio-channel-relative-peak", type=float, default=0.25, help="Skip a separated channel whose peak is far below the loudest channel")
    parser.add_argument("--audio-channel-relative-rms", type=float, default=0.20, help="Skip a separated channel whose RMS is far below the loudest channel")
    parser.add_argument("--audio-endpointing", action=argparse.BooleanOptionalAction, default=True, help="Record until speech ends instead of fixed chunks; enabled by default for Deepgram live")
    parser.add_argument("--audio-endpoint-silence-seconds", type=float, default=1.2, help="How long silence must last before an utterance is sent to Deepgram")
    parser.add_argument("--audio-endpoint-idle-seconds", type=float, default=0.6, help="How long to wait for speech before returning an empty transcript")
    parser.add_argument("--audio-speech-peak-threshold", type=int, default=120, help="Raw PCM peak threshold used to detect speech start/end")
    args = parser.parse_args()

    if not any([args.request, args.simulate, args.intervene, args.live, args.deepgram_audio, args.deepgram_live]):
        parser.error("Choose --request, --simulate, --intervene, --live, --deepgram-audio, or --deepgram-live")

    if args.deepgram_live and not args.live:
        parser.error("--deepgram-live requires --live")

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
                    "style": "passive",
                    "initiative": "reactive",
                    "role": "facilitator",
                },
                "seed_ideas": [],
                "conversation_history": [],
                "history_window_turns": 12,
                "temperature": 0.35,
                "max_tokens": 600,
                "timeout_seconds": 30.0,
                "context_fallback": "Could you tell me a little more?",
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
                "role": "facilitator",
            },
            "seed_ideas": [],
            "conversation_history": [],
            "history_window_turns": 12,
            "temperature": 0.35,
            "max_tokens": 600,
            "timeout_seconds": 30.0,
            "context_fallback": "Could you tell me a little more?",
        }

        receive_fn = console_receive
        if args.deepgram_live:
            separate_channels = resolve_audio_separate_channels(args.audio_separate_channels, args.audio_input_channels)
            if separate_channels:
                print("[Audio] Two-speaker transcript mode is on. Channel 1 and channel 2 will be transcribed separately.")
            receive_fn = build_deepgram_live_receiver(
                api_key=args.deepgram_api_key,
                endpoint=args.deepgram_endpoint,
                record_seconds=args.deepgram_record_seconds,
                input_device=args.audio_input_device,
                channels=args.audio_input_channels,
                sample_rate=args.audio_sample_rate,
                wait_for_enter=args.deepgram_press_enter,
                separate_channels=separate_channels,
                channel_names=args.audio_channel_names,
                channel_min_peak=args.audio_channel_min_peak,
                channel_relative_peak=args.audio_channel_relative_peak,
                channel_relative_rms=args.audio_channel_relative_rms,
                endpointing=args.audio_endpointing,
                endpoint_silence_seconds=args.audio_endpoint_silence_seconds,
                endpoint_idle_seconds=args.audio_endpoint_idle_seconds,
                speech_peak_threshold=args.audio_speech_peak_threshold,
            )

        if not args.pepper:
            run_live_dialog(payload, receive_fn, console_send)
            return

        send_to_pepper, pepper = build_pepper_tts_sender(args)
        print("Input uses microphone/Deepgram; Pepper will speak each LLM reply.")

        try:
            run_live_dialog(payload, receive_fn, send_to_pepper)
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
                "role": "facilitator",
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
                    style = result.get("style", "passive")
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
