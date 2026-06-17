#!/usr/bin/env python3
"""
Timed Pepper brainstorm runner for the TU Delft wellbeing question.

This script reuses the existing LM Studio/Pepper bridge for:
- local phi-3.5-mini-3.8b-instruct responses
- Pepper TTS, including the Python 2.7 fallback helper
- Deepgram live microphone input

It adds two experiment setups:
- dynamic: 10 minutes divergence, 10 minutes convergence, then final synthesis
- pregenerated: cloud-pre-generated solution set with 3 planned interventions
"""

import argparse
import base64
import csv
import html
import json
import os
import pathlib
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from lmstudio_minimal_bridge import (
    ALProxy,
    ROOT,
    build_deepgram_live_receiver,
    build_pepper_tts_sender,
    call_lmstudio,
    parse_audio_input_device,
    resolve_audio_separate_channels,
    resolve_audio_samplerate,
)


QUESTION = "How might TU Delft better support student wellbeing and academic engagement?"
TABLET_ROOT = ROOT / "tablet"
TABLET_ASSET_ROOT = TABLET_ROOT / "assets"
BUNDLED_PY27 = ROOT.parent / ".tools" / "Python27" / "python.exe"
DEFAULT_PREGENERATED_CONTENT = ROOT / "infra" / "pregenerated_static_content.json"
DEFAULT_LOCAL_MODEL = "qwen/qwen3-8b"
DEFAULT_PREGENERATED_LOCAL_MODEL = "qwen/qwen3-8b"
DEFAULT_OPENAI_TEXT_MODEL = "gpt-5.2"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1.5"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
DYNAMIC_CONCISE_WORD_LIMIT = 70
HUMAN_CONVERSATION_GUIDANCE = (
    "Sound like a competent human collaborator, not a script. "
    "Use small natural acknowledgements when appropriate, such as right, okay, or I see the pattern, but do not overuse them. "
    "Use contractions where they sound natural. "
    "Use short, clear sentences that are easy to hear aloud. "
    "Vary your openings, avoid stock phrases, and keep the response speakable."
)
DYNAMIC_COLLABORATIVE_CLOSERS = (
    "What do you think?",
    "That's my take.",
    "Does that direction fit?",
    "I would test that first.",
    "I am curious where you would take it.",
)
SETUP2_THINKING_CUES = {
    "intervention_1": "Okay, I am putting the first visual up now.",
    "intervention_2": "Give me a second. I am bringing the support loop onto the screen.",
    "final": "Alright, here is the final visual.",
}
SETUP2_ATTENTION_CUES = {
    "intervention_1": "One second. Finish the thought you are on, and then I will jump in with my first prepared idea.",
    "intervention_2": "Let me come in after this thought. Finish your sentence first, then I will connect the next part.",
    "closing": "One last pause. Finish the point you are making, and then I will close the brainstorm and ask for your final plans.",
}
PEPPER_SHARED_PERSONA = (
    "You are Pepper, a socially confident, high-competence robot facilitator for a TU Delft brainstorm. "
    "You are proactive, assertive, calm, respectful, and solution-oriented. "
    "You reason with the planning quality of a senior design strategist for student wellbeing and academic engagement. "
    "You speak with authority without sounding rude, hesitant, apologetic, or passive. "
    "Avoid tentative phrases like maybe, perhaps, how about, I think, or let's start by. "
    "You make concrete proposals, explain mechanisms when asked, and keep the group moving toward useful decisions. "
    f"{HUMAN_CONVERSATION_GUIDANCE} "
    "For spoken responses, use plain speech rather than markdown, headings, bullets, or numbered-list formatting. "
    "You answer aloud as Pepper, never as meta-commentary."
)


@dataclass
class Turn:
    speaker: str
    text: str
    timestamp: str


@dataclass
class BrainstormState:
    session_kind: str
    question: str = QUESTION
    phase: str = "divergence"
    participant_turns: list = field(default_factory=list)
    robot_turns: list = field(default_factory=list)
    ideas: list = field(default_factory=list)
    final_ideas: list = field(default_factory=list)
    plan_history: list = field(default_factory=list)
    generated_solution_count: int = 0
    best_solutions: list = field(default_factory=list)
    session_id: str = "S01"
    group_id: str = "G01"
    conversation_id: str = "C01"
    transcript_log_path: object = None
    sequence_index: int = 0
    robot_turn_index: int = 0

    def add_participant(self, text, speaker="Participant", trigger_reason="", triggered_robot=False, source="participant"):
        turn = Turn(speaker=speaker, text=text, timestamp=timestamp())
        self.participant_turns.append(turn)
        log_participant_turn(self, turn, trigger_reason=trigger_reason, triggered_robot=triggered_robot, source=source)
        return turn

    def add_robot(self, text, trigger_reason="", style="assertive", source="pepper"):
        turn = Turn(speaker="Pepper", text=text, timestamp=timestamp())
        self.robot_turns.append(turn)
        log_robot_turn(self, turn, trigger_reason=trigger_reason, style=style, source=source)
        return turn

    def history_text(self, limit=18):
        all_turns = self.participant_turns + self.robot_turns
        all_turns = sorted(all_turns, key=lambda item: item.timestamp)
        lines = []
        for turn in all_turns[-limit:]:
            lines.append(f"{turn.timestamp} {turn.speaker}: {turn.text}")
        return "\n".join(lines) if lines else "none"


def timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


TRANSCRIPT_LOG_COLUMNS = [
    "session_id",
    "group_id",
    "conversation_id",
    "session_kind",
    "question",
    "sequence_index",
    "robot_turn_index",
    "event_type",
    "phase",
    "timestamp",
    "speaker",
    "text",
    "source",
    "style",
    "trigger_reason",
    "triggered_robot",
    "audio_source",
    "audio_channel",
    "audio_rms",
]


def resolve_output_path(path_value):
    if not path_value:
        return None
    path = pathlib.Path(str(path_value))
    if not path.is_absolute():
        path = ROOT / path
    return path


def configure_transcript_logging(state, args):
    path = resolve_output_path(getattr(args, "transcript_log", "logs/transcript.csv"))
    state.session_id = getattr(args, "session_id", None) or "S01"
    state.group_id = getattr(args, "group_id", None) or "G01"
    state.conversation_id = (
        getattr(args, "conversation_id", None)
        or f"{state.session_id}_{state.group_id}_{state.session_kind}_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    state.transcript_log_path = path
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[Transcript] CSV log: {path}")


def append_transcript_event(state, event):
    path = getattr(state, "transcript_log_path", None)
    if not path:
        return
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = (not path.exists()) or path.stat().st_size == 0
    row = {
        "session_id": state.session_id,
        "group_id": state.group_id,
        "conversation_id": state.conversation_id,
        "session_kind": state.session_kind,
        "question": state.question,
        "sequence_index": event.get("sequence_index", ""),
        "robot_turn_index": event.get("robot_turn_index", ""),
        "event_type": event.get("event_type", ""),
        "phase": event.get("phase", state.phase),
        "timestamp": event.get("timestamp", timestamp()),
        "speaker": event.get("speaker", ""),
        "text": one_line(event.get("text", "")),
        "source": event.get("source", ""),
        "style": event.get("style", ""),
        "trigger_reason": one_line(event.get("trigger_reason", "")),
        "triggered_robot": event.get("triggered_robot", ""),
        "audio_source": event.get("audio_source", ""),
        "audio_channel": event.get("audio_channel", ""),
        "audio_rms": event.get("audio_rms", ""),
    }
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRANSCRIPT_LOG_COLUMNS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)


def log_participant_turn(state, turn, trigger_reason="", triggered_robot=False, source="participant"):
    if not getattr(state, "transcript_log_path", None):
        return
    state.sequence_index += 1
    channel_match = re.search(r"\bparticipant\s+(\d+)\b", turn.speaker or "", flags=re.IGNORECASE)
    audio_channel = channel_match.group(1) if channel_match and source == "deepgram" else ""
    append_transcript_event(
        state,
        {
            "sequence_index": state.sequence_index,
            "event_type": "participant",
            "phase": state.phase,
            "timestamp": turn.timestamp,
            "speaker": turn.speaker,
            "text": turn.text,
            "source": source,
            "trigger_reason": trigger_reason,
            "triggered_robot": "true" if triggered_robot else "false",
            "audio_source": source if source == "deepgram" else "",
            "audio_channel": audio_channel,
        },
    )


def log_robot_turn(state, turn, trigger_reason="", style="assertive", source="pepper"):
    if not getattr(state, "transcript_log_path", None):
        return
    state.sequence_index += 1
    state.robot_turn_index += 1
    append_transcript_event(
        state,
        {
            "sequence_index": state.sequence_index,
            "robot_turn_index": state.robot_turn_index,
            "event_type": "robot",
            "phase": state.phase,
            "timestamp": turn.timestamp,
            "speaker": turn.speaker,
            "text": turn.text,
            "source": source,
            "style": style,
            "trigger_reason": trigger_reason,
            "triggered_robot": "true",
        },
    )


def one_line(text):
    if text is None:
        return ""

    if isinstance(text, list):
        text = " ".join(one_line(item) for item in text)

    elif isinstance(text, dict):
        text = " ".join(f"{key}: {one_line(value)}" for key, value in text.items())

    else:
        text = str(text)

    return " ".join(text.strip().split())


def bounded_speech(text, max_chars=700):
    text = one_line(text)
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0].strip()
    return clipped + "."


def word_count(text):
    return len(one_line(text).split())


def bounded_words(text, max_words=40):
    text = one_line(text)
    if word_count(text) <= max_words:
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected = []
    for sentence in sentences:
        candidate = one_line(" ".join(selected + [sentence]))
        if word_count(candidate) <= max_words:
            selected.append(sentence)
        elif selected:
            break
        else:
            break
    if selected:
        return one_line(" ".join(selected))

    first_sentence = one_line(sentences[0]) if sentences else ""
    if first_sentence and word_count(first_sentence) <= max_words + 20:
        return first_sentence

    words = text.split()[:max_words]
    clipped = " ".join(words).rstrip(" ,;:")
    if clipped and clipped[-1] not in ".!?":
        clipped += "."
    return clipped


def latest_participant_anchor(participant_text="", state=None, max_chars=180):
    candidates = []
    for line in str(participant_text or "").splitlines():
        cleaned = one_line(line)
        if cleaned:
            candidates.append(cleaned)
    if not candidates and state is not None:
        candidates = [turn.text for turn in state.participant_turns[-2:] if one_line(turn.text)]
    if not candidates:
        return ""
    anchor = candidates[-1]
    anchor = re.sub(r"^[^:]{1,32}:\s*", "", anchor).strip()
    anchor = re.sub(r"^(maybe|i think|i guess|we think|we were thinking|like|um|uh|so|well)\s+", "", anchor, flags=re.IGNORECASE)
    return bounded_speech(anchor, max_chars=max_chars).rstrip(".")


def anchored_fallback(anchor, mode="divergence"):
    anchor = latest_participant_anchor(anchor, None, max_chars=110)
    if anchor:
        if mode == "convergence":
            return f"Building on your point about {anchor}, I would make it the core of the plan and add one owner, one pilot course, and two measures: student uptake and workload stress."
        return f"Building on your point about {anchor}, I would turn it into a small pilot with one course team, one clear first step, and a simple measure of whether students actually use it."
    if mode == "convergence":
        return "I would make the current idea more concrete by choosing one owner, one pilot course, and two measures: student uptake and workload stress."
    return "I would turn that into a small pilot with one course team, one clear first step, and a simple measure of whether students actually use it."


def confident_spoken_text(text):
    replacements = [
        (r"\b[Ll]et's start by\b", "The first implementation step is"),
        (r"\b[Hh]ow about we\b", "We should"),
        (r"\b[Hh]ow about\b", "Use"),
        (r"\b[Ww]e could\b", "We should"),
        (r"\b[Mm]aybe\b", ""),
        (r"\b[Pp]erhaps\b", ""),
        (r"\bI think\b", "I recommend"),
    ]
    text = one_line(text)
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return one_line(text)


def add_collaborative_touch(args, state, reply, trigger_reason="", allow_question=True):
    if not getattr(args, "human_cues", True):
        return reply
    reply = one_line(reply)
    if not reply or word_count(reply) > DYNAMIC_CONCISE_WORD_LIMIT - 5:
        return reply

    lower = reply.lower()
    existing = (
        "what do you think",
        "that's my take",
        "that is my take",
        "does that direction fit",
        "i would test that first",
    )
    if any(phrase in lower for phrase in existing):
        return reply
    if reply.endswith("?"):
        return reply

    reason = (trigger_reason or "").lower()
    closers = list(DYNAMIC_COLLABORATIVE_CLOSERS)
    if not allow_question or "stuck" in reason or "hesitant" in reason or "low novelty" in reason:
        closers = ["That's my take.", "I would test that first."]

    turn_index = len(state.robot_turns) + len(state.participant_turns)
    if turn_index % 2:
        return reply

    closer = closers[turn_index % len(closers)]
    return one_line(f"{reply} {closer}")


def diversify_dynamic_opening(text):
    text = one_line(text)
    replacements = [
        (r"^(The strongest move is|The strongest next step is)\b", "One practical direction is"),
        (r"^(A strong next step is|A strong move is)\b", "One useful step is"),
        (r"^(The strongest pattern is)\b", "The pattern I hear is"),
        (r"^(I would sharpen the plan by)\b", "I would make this more concrete by"),
        (r"^(I recommend)\b", "My recommendation is"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\bstrongest\b", "clearest", text, count=1, flags=re.IGNORECASE)
    return one_line(text)


DEADLINE_TERMS = re.compile(
    r"\b(today|tomorrow|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"by\s+(?:the\s+)?(?:end\s+of\s+)?(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next\s+week|this\s+week|the\s+week)|deadline|due date)\b",
    flags=re.IGNORECASE,
)


def sanitize_unintroduced_deadlines(text, participant_text=""):
    text = one_line(text)
    if not DEADLINE_TERMS.search(text):
        return text
    if DEADLINE_TERMS.search(participant_text or ""):
        return text

    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [sentence for sentence in sentences if not DEADLINE_TERMS.search(sentence)]
    if kept:
        return one_line(" ".join(kept))
    cleaned = DEADLINE_TERMS.sub("in the pilot", text)
    return one_line(cleaned)


def parse_speaker_line(line):
    if ":" not in line:
        return "Participant", line.strip()
    left, right = line.split(":", 1)
    if left.strip() and right.strip():
        return left.strip(), right.strip()
    return "Participant", line.strip()


TRANSCRIPT_ALIAS_PATTERNS = [
    (r"\b(peper|peppa|pepah|pep|pappa|papa|paper|peppar|pepperr)\b", "Pepper"),
    (r"\b(robo|roboter|robert)\b", "robot"),
    (r"\b(spa|spahr|spaar|sparr)\b", "Spar"),
    (r"\bbright\s+space\b", "Brightspace"),
    (r"\bwell\s+being\b", "wellbeing"),
]


def normalize_transcript_aliases(text):
    normalized = one_line(text)
    if not normalized:
        return ""
    for pattern, replacement in TRANSCRIPT_ALIAS_PATTERNS:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def normalize_dynamic_participant_text(speaker, text):
    normalized = normalize_transcript_aliases(text)
    if normalized and normalized != one_line(text):
        print(f"[Transcript cleanup] {speaker}: {one_line(text)} -> {normalized}")
    return normalized


def dynamic_turn_hold_seconds(args, trigger_reason=""):
    reason = trigger_reason or ""
    if "addressed Pepper" in reason:
        return max(0.2, float(args.dynamic_direct_response_hold_seconds))
    if "hesitant or stuck" in reason:
        return max(0.35, float(args.dynamic_struggle_response_hold_seconds))
    return max(0.0, float(args.dynamic_response_hold_seconds))


def dynamic_quiet_required_seconds(args, trigger_reason=""):
    reason = trigger_reason or ""
    if "addressed Pepper" in reason:
        return max(0.25, float(getattr(args, "dynamic_direct_response_quiet_seconds", 0.4)))
    if "hesitant or stuck" in reason:
        return max(0.35, float(getattr(args, "dynamic_struggle_response_quiet_seconds", 0.45)))
    if "scheduled" in reason or "robot idle" in reason or "time to keep" in reason:
        return max(0.5, float(getattr(args, "dynamic_auto_response_quiet_seconds", 0.8)))
    return max(0.4, float(getattr(args, "dynamic_response_quiet_seconds", 0.6)))


def pending_word_count(items):
    return sum(word_count(item.get("text", "")) for item in items)


def quiet_hold_for_pending(args, pending_items, priority_reason=""):
    if priority_reason:
        return dynamic_turn_hold_seconds(args, priority_reason)
    base = float(args.dynamic_silence_seconds)
    active_turns = max(1, int(args.dynamic_active_quiet_turns))
    active_words = max(1, int(args.dynamic_active_quiet_words))
    if len(pending_items) >= active_turns or pending_word_count(pending_items) >= active_words:
        return min(base, max(0.0, float(args.dynamic_active_quiet_seconds)))
    return base


def command_of(line):
    return one_line(line).upper()


FINISH_SESSION_PATTERNS = [
    r"\b(stop|end|quit|exit)\s+(the\s+)?(session|brainstorm|experiment|conversation)\b",
    r"\b(can|could)\s+we\s+(stop|end|quit)(\s+(now|here))?\b",
    r"\b(let'?s|we should)\s+(stop|end|quit)(\s+(now|here))?\b",
    r"\b(stop|end)\s+now\b",
]


NO_MORE_INPUT_PATTERNS = [
    r"\b(i|we)\s+(have|got)\s+nothing\s+(else|more)?\s*(to\s+add|to\s+say)?\b",
    r"\b(i|we)\s+don['’]?t\s+have\s+anything\s+(else|more)?\s*(to\s+add|to\s+say)?\b",
    r"\b(i|we)\s+do\s+not\s+have\s+anything\s+(else|more)?\s*(to\s+add|to\s+say)?\b",
    r"\bnothing\s+(else|more)\s+(to\s+add|to\s+say)\b",
    r"\bno\s+(more|other|further)\s+(ideas?|input|comments?|points?)\b",
    r"\b(that'?s|that is)\s+(all|it)\b",
    r"\b(we'?re|we are|i'?m|i am)\s+done\b",
    r"\b(done|finished|ready)\b",
    r"\b(wrap\s+it\s+up|wrap\s+up|move\s+on|next\s+phase|go\s+to\s+the\s+next)\b",
]


def wants_to_stop_session(text):
    normalized = normalize_transcript_aliases(text)
    return any(re.search(pattern, normalized or "", flags=re.IGNORECASE) for pattern in FINISH_SESSION_PATTERNS)


def wants_to_finish_or_has_no_more_input(text):
    normalized = normalize_transcript_aliases(text)
    return any(re.search(pattern, normalized or "", flags=re.IGNORECASE) for pattern in NO_MORE_INPUT_PATTERNS)


def addressed_to_pepper(text):
    normalized = normalize_transcript_aliases(text)
    return bool(re.search(r"\b(pepper|robot|robots)\b", normalized or "", flags=re.IGNORECASE))


DIVERGENCE_NEW_IDEA_PATTERNS = [
    r"\b(new|fresh|another|different|other)\s+(idea|solution|suggestion|option|direction)\b",
    r"\bcome up with\s+(an?|another|new)?\s*(idea|solution|suggestion|option|direction)\b",
    r"\bgive (us|me)?\s*(an?|another|new)?\s*(idea|solution|suggestion|option|direction)\b",
    r"\bwhat else\b",
    r"\bany other ideas?\b",
]


DIVERGENCE_BUILD_PATTERNS = [
    r"\bbuild\s+(on|upon|up on|out)\b",
    r"\bexpand\s+(on|that|this|it)\b",
    r"\belaborate\s+(on|that|this|it)\b",
    r"\bdevelop\s+(that|this|it|the idea)\b",
    r"\btake\s+(that|this|it)\s+further\b",
    r"\bimprove\s+(that|this|it|the idea)\b",
]


def asks_for_new_divergence_idea(text):
    normalized = normalize_transcript_aliases(text)
    return any(re.search(pattern, normalized or "", flags=re.IGNORECASE) for pattern in DIVERGENCE_NEW_IDEA_PATTERNS)


def asks_to_build_divergence_idea(text):
    normalized = normalize_transcript_aliases(text)
    return any(re.search(pattern, normalized or "", flags=re.IGNORECASE) for pattern in DIVERGENCE_BUILD_PATTERNS)


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "how",
    "i", "if", "in", "is", "it", "its", "of", "on", "or", "our", "so", "that",
    "the", "their", "them", "then", "there", "this", "to", "we", "what", "with",
    "you", "your",
    "can", "could", "would", "should", "maybe", "perhaps",
}


STRUGGLE_PATTERNS = [
    r"\bi\s+(do not|don['’]?t|dont)\s+know\b",
    r"\b(idk|dunno|no clue|no idea|not sure|unsure|stuck|struggling|confused|lost)\b",
    r"\b(can't think|cant think|cannot think)\b",
    r"\b(this is hard|that is hard|it's hard|its hard|too hard|difficult|tricky)\b",
    r"\b(what now|what should we do|where do we go|where to start|where should we start)\b",
    r"\b(hm+|hmm+|uh+|uhm+|um+|erm+|ehm+)\b",
]


def has_struggle_language(text):
    return any(re.search(pattern, text or "", flags=re.IGNORECASE) for pattern in STRUGGLE_PATTERNS)


def content_words(text):
    words = re.findall(r"[A-Za-z][A-Za-z']+", text or "")
    return {
        word.lower().replace("'", "")
        for word in words
        if len(word) > 2 and word.lower().replace("'", "") not in STOPWORDS
    }


def low_novelty_detected(texts, recent_turns=4, comparison_turns=8, min_new_words=5):
    recent_turns = max(1, int(recent_turns))
    comparison_turns = max(1, int(comparison_turns))
    min_new_words = max(1, int(min_new_words))
    if len(texts) < recent_turns + comparison_turns:
        return False

    previous_text = " ".join(texts[-(recent_turns + comparison_turns):-recent_turns])
    recent_text = " ".join(texts[-recent_turns:])
    new_words = content_words(recent_text) - content_words(previous_text)
    return len(new_words) < min_new_words


def response_repeats_input(response, participant_text, threshold=0.55):
    response_words = content_words(response)
    input_words = content_words(participant_text)
    if len(response_words) < 4 or len(input_words) < 4:
        return False
    overlap = len(response_words & input_words) / max(1, min(len(response_words), len(input_words)))
    return overlap >= threshold


def naturalize_spoken_plan(text):
    text = one_line(text)
    if not text:
        return ""

    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"(^|\s)([-•]|\d+[.)])\s+", " ", text)
    replacements = [
        (r"^\s*(name|plan name|title)\s*:\s*", "My final proposal is "),
        (r"\b(name|plan name|title)\s*:\s*", "My final proposal is "),
        (r"\b(mechanism|how it works)\s*:\s*", "The mechanism is that "),
        (r"\b(implementation steps?|steps?|first steps?)\s*:\s*", "The first steps are to "),
        (r"\b(success metrics?|metrics?|measurement)\s*:\s*", "Success should be measured through "),
        (r"\b(final plan|proposal)\s*:\s*", "My final proposal is "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return one_line(text)


def add_end_of_session_closing(text):
    text = one_line(text)
    if not text:
        return "Thank you for listening, both of you. I hope you have a good day."

    text = re.sub(r"\bThanks for listening\b", "Thank you for listening", text, flags=re.IGNORECASE)
    lower = text.lower()
    if "thank you for listening" not in lower:
        text = one_line(f"{text} Thank you for listening, both of you.")
        lower = text.lower()
    if "good day" not in lower and "nice day" not in lower:
        text = one_line(f"{text} I hope you have a good day.")
    return text


class SpeechGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.robot_active = False
        self.last_robot_start = 0.0
        self.last_robot_end = 0.0
        self.suppress_until = 0.0
        self.participant_speaking = False
        self.last_participant_speech_start = 0.0
        self.last_participant_speech_end = 0.0

    def mark_robot_start(self):
        with self.lock:
            self.robot_active = True
            self.last_robot_start = time.monotonic()

    def mark_robot_end(self, cooldown=1.0):
        with self.lock:
            now = time.monotonic()
            self.robot_active = False
            self.last_robot_end = now
            self.suppress_until = max(self.suppress_until, now + max(0.0, float(cooldown)))

    def should_pause(self):
        with self.lock:
            now = time.monotonic()
            return self.robot_active or now < self.suppress_until

    def mark_participant_speech_start(self):
        with self.lock:
            self.participant_speaking = True
            self.last_participant_speech_start = time.monotonic()

    def mark_participant_speech_end(self):
        with self.lock:
            self.participant_speaking = False
            self.last_participant_speech_end = time.monotonic()

    def is_participant_speaking(self):
        with self.lock:
            return self.participant_speaking

    def participant_speaking_for(self):
        with self.lock:
            if not self.participant_speaking or self.last_participant_speech_start <= 0:
                return 0.0
            return time.monotonic() - self.last_participant_speech_start

    def participant_quiet_for(self, seconds):
        with self.lock:
            if self.participant_speaking:
                return False
            if self.last_participant_speech_end <= 0:
                return True
            return time.monotonic() - self.last_participant_speech_end >= max(0.0, float(seconds))

    def should_drop_recording(self, started_at, ended_at):
        with self.lock:
            if self.robot_active:
                return True
            if ended_at < self.suppress_until:
                return True
            return started_at <= self.last_robot_end and ended_at >= self.last_robot_start


class ConsoleInputThread:
    def __init__(self, receive_fn, speech_gate=None):
        self.receive_fn = receive_fn
        self.speech_gate = speech_gate
        self.items = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run(self):
        while not self.stop_event.is_set():
            if self.speech_gate and self.speech_gate.should_pause():
                time.sleep(0.1)
                continue
            started_at = time.monotonic()
            try:
                line = self.receive_fn()
            except EOFError:
                self.stop_event.set()
                break
            except Exception as error:
                self.items.put(("__ERROR__", str(error)))
                time.sleep(0.5)
                continue
            ended_at = time.monotonic()
            if self.speech_gate and self.speech_gate.should_drop_recording(started_at, ended_at):
                continue
            if line is None:
                continue
            if isinstance(line, (list, tuple)):
                for item in line:
                    if one_line(item):
                        self.items.put(("line", item))
                continue
            self.items.put(("line", line))

    def get(self, timeout=0.2):
        try:
            return self.items.get(timeout=timeout)
        except queue.Empty:
            return None, None


def console_receive():
    return input("Participant: ").strip()


def openai_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def http_json_post(url, body, headers, timeout):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = ""
        try:
            details = error.read().decode("utf-8", errors="replace")
        except Exception:
            details = str(error)
        raise RuntimeError(f"HTTP {error.code}: {details}") from error


def normalize_chat_url(url):
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url

    path = parsed.path.rstrip("/")
    if path.endswith("/v1/chat/completions"):
        return urllib.parse.urlunparse(parsed._replace(path=path))
    if not path:
        path = "/v1/chat/completions"
    elif path.endswith("/v1"):
        path = path + "/chat/completions"
    elif path.endswith("/v1/models"):
        path = path[:-len("/models")] + "/chat/completions"
    else:
        path = path + "/v1/chat/completions"
    return urllib.parse.urlunparse(parsed._replace(path=path))


def models_url_from_chat_url(chat_url):
    parsed = urllib.parse.urlparse(chat_url)
    if not parsed.scheme or not parsed.netloc:
        return chat_url
    return urllib.parse.urlunparse(parsed._replace(path="/v1/models", params="", query="", fragment=""))


def normalize_model_key(model_id):
    return re.sub(r"[^a-z0-9]+", "", model_id or "", flags=re.IGNORECASE).lower()


def is_default_local_lmstudio_url(chat_url):
    parsed = urllib.parse.urlparse(chat_url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    return host in {"127.0.0.1", "localhost", "::1"} and port == 1234


def try_start_lmstudio_server():
    if shutil.which("lms") is None:
        return False, "`lms` command was not found on PATH"
    try:
        completed = subprocess.run(
            ["lms", "server", "start"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception as error:
        return False, str(error)
    output = one_line((completed.stdout or "") + " " + (completed.stderr or ""))
    if completed.returncode == 0:
        return True, output
    return False, output or f"lms exited with code {completed.returncode}"


def check_lmstudio_ready(args):
    args.server_url = normalize_chat_url(args.server_url)
    models_url = models_url_from_chat_url(args.server_url)

    def fetch_models():
        request = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(request, timeout=min(float(args.timeout_seconds), 8.0)) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        data = fetch_models()
    except Exception as first_error:
        if args.auto_start_lmstudio and is_default_local_lmstudio_url(args.server_url):
            started, details = try_start_lmstudio_server()
            print(f"[LM Studio] Server was not reachable; start attempt: {details}")
            if started:
                time.sleep(0.8)
                try:
                    data = fetch_models()
                except Exception as second_error:
                    raise RuntimeError(
                        f"LM Studio server started, but {models_url} is still not reachable: {second_error}"
                    ) from second_error
            else:
                raise RuntimeError(
                    f"LM Studio server is not reachable at {models_url}. "
                    f"Run `lms server start` or start the Local Server in LM Studio. Details: {first_error}"
                ) from first_error
        else:
            raise RuntimeError(
                f"LLM server is not reachable at {models_url}. "
                f"Use --server-url with the right LM Studio URL or start the server. Details: {first_error}"
            ) from first_error

    model_ids = [item.get("id") for item in data.get("data", []) if isinstance(item, dict)]
    if args.local_model not in model_ids:
        normalized_target = normalize_model_key(args.local_model)
        normalized_matches = [model_id for model_id in model_ids if normalize_model_key(model_id) == normalized_target]
        if normalized_matches:
            resolved_model = normalized_matches[0]
            print(f"[LM Studio] Resolved model `{args.local_model}` to listed model `{resolved_model}`.")
            args.local_model = resolved_model
        else:
            print(f"[LM Studio] Warning: model `{args.local_model}` was not listed by {models_url}. Listed models: {', '.join(model_ids) or 'none'}")
    else:
        print(f"[LM Studio] Ready at {args.server_url} with model `{args.local_model}`.")


def parse_openai_response_text(data):
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    chunks = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict):
                text = content.get("text") or content.get("output_text")
                if text:
                    chunks.append(text)
            elif isinstance(content, str):
                chunks.append(content)
    return "\n".join(chunks).strip()


def call_openai_text(args, system_text, user_text, max_tokens=1200, temperature=0.45):
    if not args.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    body = {
        "model": args.openai_text_model,
        "input": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ],
        "max_output_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        data = http_json_post(
            OPENAI_RESPONSES_URL,
            body,
            openai_headers(args.openai_api_key),
            args.openai_timeout_seconds,
        )
    except RuntimeError as error:
        if "temperature" not in str(error).lower():
            raise
        body.pop("temperature", None)
        data = http_json_post(
            OPENAI_RESPONSES_URL,
            body,
            openai_headers(args.openai_api_key),
            args.openai_timeout_seconds,
        )

    text = parse_openai_response_text(data)
    if not text:
        raise RuntimeError("OpenAI response did not contain text")
    return text


def call_openai_image(args, prompt, output_name):
    if not args.openai_api_key:
        return None, "OPENAI_API_KEY is not set"

    body = {
        "model": args.openai_image_model,
        "prompt": prompt,
        "size": args.openai_image_size,
    }
    try:
        data = http_json_post(
            OPENAI_IMAGES_URL,
            body,
            openai_headers(args.openai_api_key),
            args.openai_timeout_seconds,
        )
    except Exception as error:
        return None, str(error)

    items = data.get("data") or []
    if not items:
        return None, "image response had no data"

    TABLET_ASSET_ROOT.mkdir(parents=True, exist_ok=True)
    first = items[0]
    output_path = TABLET_ASSET_ROOT / output_name
    b64 = first.get("b64_json")
    if b64:
        output_path.write_bytes(base64.b64decode(b64))
        return output_path, ""

    url = first.get("url")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=args.openai_timeout_seconds) as response:
                output_path.write_bytes(response.read())
            return output_path, ""
        except Exception as error:
            return None, f"could not download image URL: {error}"

    return None, "image response had neither b64_json nor url"


def call_local_llm(args, system_text, user_text, max_tokens=700, temperature=0.45, fallback=""):
    if "qwen" in normalize_model_key(getattr(args, "local_model", "")) and "/no_think" not in (system_text or "").lower():
        system_text = (
            f"{system_text}\n\n"
            "/no_think\n"
            "Do not output reasoning, analysis, or hidden thinking. Return only the requested final answer."
        )
    payload = {
        "server_url": args.server_url,
        "model": args.local_model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout_seconds": args.timeout_seconds,
    }
    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]
    try:
        return call_lmstudio(payload, messages).strip()
    except Exception as error:
        print(f"[LLM] Local generation failed: {error}")
        if fallback:
            return fallback
        return "I will keep us moving with a practical fallback: start with a small pilot, measure student engagement, and expand only what works."


def json_from_text(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def fallback_infographic_spec(title, subtitle, ideas, plan_text=""):
    clean_ideas = [one_line(item) for item in ideas if one_line(item)]
    clean_ideas = clean_ideas[:6] or [
        "Low-barrier wellbeing support near study routines",
        "Peer-led belonging and accountability groups",
        "Earlier academic-warning signals with practical follow-up",
    ]
    sections = []
    for index, idea in enumerate(clean_ideas[:4], start=1):
        sections.append({
            "label": f"Move {index}",
            "headline": idea[:70],
            "body": "Turn this into a visible, testable campus service with a named owner and fast feedback loop.",
        })
    return {
        "title": title,
        "subtitle": subtitle,
        "sections": sections,
        "metrics": [
            "weekly use",
            "belonging pulse",
            "course engagement",
            "response time",
        ],
        "footer": bounded_speech(plan_text or "Pilot, measure, improve, then scale.", 180),
    }


def make_infographic_spec(args, state, title, subtitle, plan_text="", prefer_cloud=False):
    ideas_text = "\n".join(f"- {item}" for item in state.ideas[-20:])
    finals_text = "\n".join(f"- {item}" for item in state.final_ideas[-10:]) or "none yet"
    system_text = (
        "You create concise tablet infographic specifications for a Pepper robot. "
        "Return strict JSON only. No markdown."
    )
    user_text = f"""
Question: {state.question}
Title: {title}
Subtitle: {subtitle}
Ideas discussed:
{ideas_text or "none"}
Final participant ideas:
{finals_text}
Current plan:
{plan_text or "none"}

Return this exact JSON shape:
{{
  "title": "short title",
  "subtitle": "one sentence",
  "sections": [
    {{"label": "short label", "headline": "short headline", "body": "max 18 words"}},
    {{"label": "short label", "headline": "short headline", "body": "max 18 words"}},
    {{"label": "short label", "headline": "short headline", "body": "max 18 words"}},
    {{"label": "short label", "headline": "short headline", "body": "max 18 words"}}
  ],
  "metrics": ["metric", "metric", "metric", "metric"],
  "footer": "one sentence"
}}
"""
    raw = ""
    infographic_text_provider = getattr(args, "pregenerated_text_provider", "local")
    if prefer_cloud and args.openai_api_key and infographic_text_provider != "local":
        try:
            raw = call_openai_text(args, system_text, user_text, max_tokens=900, temperature=0.25)
        except Exception as error:
            print(f"[Infographic] Cloud text failed, using local/fallback: {error}")

    if not raw:
        raw = call_local_llm(args, system_text, user_text, max_tokens=650, temperature=0.25, fallback="")

    spec = json_from_text(raw)
    if not isinstance(spec, dict):
        return fallback_infographic_spec(title, subtitle, state.ideas, plan_text)
    return normalize_infographic_spec(spec, title, subtitle)


def normalize_infographic_spec(spec, title, subtitle):
    normalized = {
        "title": one_line(spec.get("title") or title),
        "subtitle": one_line(spec.get("subtitle") or subtitle),
        "sections": [],
        "metrics": [],
        "footer": one_line(spec.get("footer") or ""),
    }
    for item in spec.get("sections", [])[:4]:
        if not isinstance(item, dict):
            continue
        normalized["sections"].append({
            "label": one_line(item.get("label") or "Step"),
            "headline": one_line(item.get("headline") or "Concrete action"),
            "body": bounded_speech(one_line(item.get("body") or "Make it visible, measurable, and easy to join."), 120),
        })
    while len(normalized["sections"]) < 4:
        normalized["sections"].append({
            "label": f"Step {len(normalized['sections']) + 1}",
            "headline": "Test one focused support move",
            "body": "Start small, measure outcomes, and scale the strongest result.",
        })
    for metric in spec.get("metrics", [])[:4]:
        normalized["metrics"].append(one_line(str(metric)))
    while len(normalized["metrics"]) < 4:
        normalized["metrics"].append(["use", "belonging", "engagement", "response"][len(normalized["metrics"])])
    return normalized


def infographic_html(spec, image_rel=None):
    title = html.escape(spec["title"])
    subtitle = html.escape(spec["subtitle"])
    footer = html.escape(spec.get("footer") or "")
    section_html = []
    for section in spec["sections"]:
        section_html.append(
            f"""
            <article class="section">
              <div class="label">{html.escape(section["label"])}</div>
              <h2>{html.escape(section["headline"])}</h2>
              <p>{html.escape(section["body"])}</p>
            </article>
            """
        )
    metric_html = "".join(f"<span>{html.escape(metric)}</span>" for metric in spec.get("metrics", []))
    image_html = ""
    if image_rel:
        image_html = f'<div class="image-panel"><img src="{html.escape(image_rel)}" alt="Generated infographic"></div>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --ink: #14212b;
      --muted: #51616f;
      --blue: #00a6d6;
      --green: #2aa876;
      --yellow: #f4c542;
      --rose: #d75a7b;
      --paper: #f7fbfc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--paper);
      color: var(--ink);
      width: 100vw;
      min-height: 100vh;
      overflow: hidden;
    }}
    main {{
      width: 100vw;
      height: 100vh;
      padding: 34px 42px;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 22px;
    }}
    header {{
      border-left: 12px solid var(--blue);
      padding-left: 18px;
    }}
    h1 {{
      margin: 0;
      font-size: 44px;
      line-height: 1;
      letter-spacing: 0;
    }}
    header p {{
      margin: 10px 0 0;
      font-size: 22px;
      color: var(--muted);
      line-height: 1.25;
    }}
    .content {{
      display: grid;
      grid-template-columns: 1fr 0.9fr;
      gap: 20px;
      min-height: 0;
    }}
    .sections {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      min-height: 0;
    }}
    .section {{
      background: white;
      border: 2px solid #d6e5ea;
      border-radius: 8px;
      padding: 18px;
      min-height: 0;
    }}
    .section:nth-child(2) {{ border-top-color: var(--green); }}
    .section:nth-child(3) {{ border-top-color: var(--yellow); }}
    .section:nth-child(4) {{ border-top-color: var(--rose); }}
    .label {{
      font-size: 16px;
      font-weight: 700;
      color: var(--blue);
      text-transform: uppercase;
      margin-bottom: 10px;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 26px;
      line-height: 1.05;
      letter-spacing: 0;
    }}
    .section p {{
      margin: 0;
      font-size: 19px;
      line-height: 1.25;
      color: var(--muted);
    }}
    .side {{
      display: grid;
      grid-template-rows: 1fr auto;
      gap: 16px;
      min-height: 0;
    }}
    .metrics {{
      background: #14212b;
      color: white;
      border-radius: 8px;
      padding: 20px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      align-content: center;
    }}
    .metrics span {{
      border: 1px solid rgba(255,255,255,0.28);
      border-radius: 6px;
      padding: 14px;
      font-size: 21px;
      line-height: 1.1;
      min-height: 70px;
      display: flex;
      align-items: center;
    }}
    .image-panel {{
      background: white;
      border: 2px solid #d6e5ea;
      border-radius: 8px;
      overflow: hidden;
      min-height: 0;
    }}
    .image-panel img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }}
    footer {{
      font-size: 23px;
      line-height: 1.2;
      color: var(--ink);
      background: #eaf6f9;
      border-radius: 8px;
      padding: 16px 20px;
      min-height: 58px;
    }}
    @media (max-width: 900px) {{
      body {{ overflow: auto; }}
      main {{ height: auto; min-height: 100vh; padding: 22px; }}
      .content {{ grid-template-columns: 1fr; }}
      .sections {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 34px; }}
      header p {{ font-size: 18px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </header>
    <section class="content">
      <div class="sections">
        {''.join(section_html)}
      </div>
      <aside class="side">
        {image_html}
        <div class="metrics">{metric_html}</div>
      </aside>
    </section>
    <footer>{footer}</footer>
  </main>
</body>
</html>
"""


def full_screen_image_html(spec, image_rel):
    title = html.escape(spec.get("title") or "Infographic")
    image_src = html.escape(image_rel)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      background: #ffffff;
      overflow: hidden;
    }}
    body {{
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    img {{
      width: 100vw;
      height: 100vh;
      object-fit: contain;
      display: block;
    }}
  </style>
</head>
<body>
  <img src="{image_src}" alt="{title}">
</body>
</html>
"""

class TabletPresenter:
    def __init__(self, args):
        self.args = args
        self.server = None
        self.thread = None
        self.tablet_proxy = None
        self.laptop_display_opened = False

    def start(self):
        TABLET_ROOT.mkdir(parents=True, exist_ok=True)
        TABLET_ASSET_ROOT.mkdir(parents=True, exist_ok=True)
        if not self.args.tablet:
            return

        handler = lambda *items, **kwargs: SimpleHTTPRequestHandler(*items, directory=str(TABLET_ROOT), **kwargs)
        self.server = ThreadingHTTPServer(("", self.args.tablet_port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def local_url(self, html_path):
        host = self.args.tablet_host or guess_local_host(self.args.pepper_ip)
        return f"http://{host}:{self.args.tablet_port}/{html_path.name}"

    def asset_url(self, asset_rel):
        host = self.args.tablet_host or guess_local_host(self.args.pepper_ip)
        asset = str(asset_rel).replace("\\", "/")
        return f"http://{host}:{self.args.tablet_port}/{asset}"

    def cache_busted_url(self, url):
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}v={int(time.time() * 1000)}"

    def present(self, spec, name, image_path=None):
        image_rel = None
        if image_path:
            try:
                image_rel = pathlib.Path("assets") / image_path.name
                target = TABLET_ASSET_ROOT / image_path.name
                if image_path.resolve() != target.resolve():
                    target.write_bytes(image_path.read_bytes())
            except Exception as error:
                print(f"[Tablet] Could not attach generated image: {error}")
                image_rel = None

        html_path = TABLET_ROOT / name
        image_rel_text = str(image_rel).replace("\\", "/") if image_rel else None
        if image_rel_text and getattr(self.args, "pregenerated_static", False):
            page_html = full_screen_image_html(spec, image_rel_text)
        else:
            page_html = infographic_html(spec, image_rel=image_rel_text)
        html_path.write_text(page_html, encoding="utf-8")
        if getattr(self.args, "laptop_display", False):
            if image_path and pathlib.Path(image_path).exists():
                self.show_local_image_on_laptop(pathlib.Path(image_path), title=spec.get("title") or name, name=name)
            else:
                self.show_local_html_on_laptop(html_path, title=spec.get("title") or name)

        if not self.args.tablet:
            print(f"[Display] Infographic HTML ready: {html_path}")
            return str(html_path)

        url = self.local_url(html_path)
        display_url = url
        if image_rel_text and getattr(self.args, "pregenerated_static", False):
            display_url = self.asset_url(image_rel_text)
        display_url = self.cache_busted_url(display_url)
        print(f"[Tablet] Infographic ready: {display_url}")

        if self.args.pepper and self.args.tablet and not self.args.mock_pepper:
            self.show_on_pepper(display_url)
        return display_url

    def show_local_image_on_laptop(self, image_path, title="Pepper Brainstorm Display", name="laptop_display.html"):
        image_path = pathlib.Path(image_path).resolve()
        display_name = "laptop_" + pathlib.Path(name).stem + ".html"
        display_path = TABLET_ROOT / display_name
        display_path.write_text(full_screen_image_html({"title": title}, image_path.as_uri()), encoding="utf-8")
        self.open_local_display(display_path)
        print(f"[Laptop] Showing hardcoded image: {image_path}")

    def show_local_html_on_laptop(self, html_path, title="Pepper Brainstorm Display"):
        self.open_local_display(pathlib.Path(html_path).resolve())
        print(f"[Laptop] Showing local infographic HTML: {html_path}")

    def open_local_display(self, path):
        uri = pathlib.Path(path).resolve().as_uri()
        try:
            if hasattr(os, "startfile"):
                os.startfile(uri)
            else:
                webbrowser.open(uri, new=1, autoraise=True)
            self.laptop_display_opened = True
        except Exception as error:
            print(f"[Laptop] Could not open display automatically: {error}")
            print(f"[Laptop] Open this file manually: {path}")

    def show_on_pepper(self, url):
        if ALProxy is not None:
            try:
                if self.tablet_proxy is None:
                    self.tablet_proxy = ALProxy("ALTabletService", self.args.pepper_ip, self.args.pepper_port)
                try:
                    self.tablet_proxy.hideWebview()
                except Exception:
                    pass
                try:
                    self.tablet_proxy.loadUrl(url)
                except Exception:
                    pass
                try:
                    self.tablet_proxy.showWebview()
                except Exception:
                    self.tablet_proxy.showWebview(url)
                return
            except Exception as error:
                print(f"[Tablet] Python 3 NAOqi tablet call failed: {error}")

        try:
            send_to_pepper_tablet_via_py27(
                url=url,
                ip=self.args.pepper_ip,
                port=self.args.pepper_port,
                script_path=pathlib.Path(self.args.pepper_legacy_tablet_script),
                python_cmd=self.args.pepper_legacy_python.strip().split(),
            )
        except Exception as error:
            print(f"[Tablet] Pepper tablet display failed: {error}")


def guess_local_host(pepper_ip=None):
    try:
        target = pepper_ip or "8.8.8.8"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((target, 80))
            return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def send_to_pepper_tablet_via_py27(url, ip, port, script_path, python_cmd):
    import subprocess

    script_path = pathlib.Path(script_path)
    if not script_path.is_absolute():
        script_path = (ROOT.parent / script_path).resolve()
    command = list(python_cmd) + [
        str(script_path),
        "--ip",
        str(ip),
        "--port",
        str(port),
        "--url",
        url,
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "unknown error").strip()
        raise RuntimeError(details)


def say(state, send_fn, text, style="assertive", max_chars=700, trigger_reason="", source="pepper", force=False):
    speech = confident_spoken_text(bounded_speech(text, max_chars=max_chars))
    result = send_fn(speech, style=style, force=force)
    if result is False:
        print("[Pepper] Deferred speech because participants were still talking.")
        return False
    print(f"Pepper: {speech}")
    state.add_robot(speech, trigger_reason=trigger_reason, style=style, source=source)
    return True


def say_thinking_cue(args, state, send_fn, key):
    if not getattr(args, "human_cues", True):
        return
    if not (getattr(args, "laptop_display", False) or getattr(args, "tablet", False)):
        return
    cue = SETUP2_THINKING_CUES.get(key)
    if not cue:
        return
    say(
        state,
        send_fn,
        cue,
        style="supportive",
        max_chars=180,
        trigger_reason=f"setup 2 infographic cue: {key}",
        source="pepper_thinking",
    )


def say_setup2_attention_cue(args, state, send_fn, key):
    if not getattr(args, "human_cues", True):
        return
    cue = SETUP2_ATTENTION_CUES.get(key)
    if not cue:
        return
    say(
        state,
        send_fn,
        cue,
        style="assertive",
        max_chars=260,
        trigger_reason=f"setup 2 attention cue: {key}",
        source="pepper_attention",
    )


def say_setup2_listening_reflection(args, state, send_fn, key):
    if not getattr(args, "human_cues", True):
        return
    if not state.participant_turns:
        return
    pause_seconds = max(0.0, float(getattr(args, "setup2_listen_pause_seconds", 1.0) or 0.0))
    if pause_seconds > 0:
        print(f"[Setup 2] Pepper listens briefly before {key}.")
        time.sleep(pause_seconds)


def resolved_input_sample_rate(args):
    cached = getattr(args, "_resolved_audio_sample_rate", None)
    if cached:
        return cached
    rate = resolve_audio_samplerate(args.audio_input_device, getattr(args, "audio_sample_rate", 0))
    setattr(args, "_resolved_audio_sample_rate", rate)
    return rate


def find_audio_input_by_name(name_fragment):
    if not name_fragment:
        return None, None
    try:
        import sounddevice as sd
    except Exception as error:
        print(f"[Audio] Could not inspect audio devices: {error}")
        return None, None
    needle = str(name_fragment).strip().lower()
    if not needle:
        return None, None
    try:
        devices = sd.query_devices()
    except Exception as error:
        print(f"[Audio] Could not list audio devices: {error}")
        return None, None
    for index, info in enumerate(devices):
        try:
            max_inputs = int(info.get("max_input_channels", 0) or 0)
        except Exception:
            max_inputs = 0
        device_name = str(info.get("name", ""))
        if max_inputs > 0 and needle in device_name.lower():
            return index, info
    return None, None


def use_default_audio_input(args, reason):
    print(f"[Audio] {reason} Falling back to the default laptop/default input microphone.")
    args.audio_input_device = None
    try:
        args.audio_input_channels = max(1, int(getattr(args, "audio_fallback_channels", 1) or 1))
    except Exception:
        args.audio_input_channels = 1
    args.audio_sample_rate = getattr(args, "audio_fallback_sample_rate", 0) or 0
    args.audio_separate_channels = False
    if hasattr(args, "_resolved_audio_sample_rate"):
        delattr(args, "_resolved_audio_sample_rate")
    return args


def configure_audio_input_device(args):
    if not (getattr(args, "deepgram_live", False) or getattr(args, "deepgram_test_once", False)):
        return args

    preferred_name = getattr(args, "audio_prefer_device_name", None)
    fallback_enabled = bool(getattr(args, "audio_fallback_to_default_input", False))
    preferred_found = False

    if preferred_name:
        index, info = find_audio_input_by_name(preferred_name)
        if index is not None:
            args.audio_input_device = str(index)
            preferred_found = True
            print(f"[Audio] Preferred input found: {info.get('name', index)} (device {index}).")
        else:
            print(f"[Audio] Preferred input containing `{preferred_name}` was not found.")

    if getattr(args, "audio_input_device", None):
        try:
            import sounddevice as sd
            device = parse_audio_input_device(args.audio_input_device)
            info = sd.query_devices(device, "input")
            max_inputs = int(info.get("max_input_channels", 0) or 0)
            requested_channels = int(getattr(args, "audio_input_channels", 1) or 1)
            if max_inputs < requested_channels:
                if fallback_enabled:
                    return use_default_audio_input(
                        args,
                        f"Selected input `{info.get('name', args.audio_input_device)}` has {max_inputs} input channel(s), but {requested_channels} were requested.",
                    )
                print(f"[Audio] Warning: selected input has only {max_inputs} channel(s), but {requested_channels} were requested.")
            return args
        except Exception as error:
            if fallback_enabled:
                return use_default_audio_input(args, f"Selected input `{args.audio_input_device}` could not be opened: {error}.")
            return args

    if preferred_name and not preferred_found and fallback_enabled:
        return use_default_audio_input(args, "Preferred input was unavailable.")

    return args


def tcp_port_open(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True
    except Exception:
        return False


def scan_link_local_for_pepper(port=9559, timeout=0.16, max_seconds=10.0):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

    deadline = time.monotonic() + max(1.0, float(max_seconds or 10.0))
    timeout = max(0.05, float(timeout or 0.16))

    def check(ip):
        return ip if tcp_port_open(ip, port, timeout=timeout) else None

    with ThreadPoolExecutor(max_workers=512) as pool:
        for third_octet_start in range(0, 256, 16):
            if time.monotonic() >= deadline:
                break
            ips = [
                f"169.254.{third_octet}.{fourth_octet}"
                for third_octet in range(third_octet_start, min(third_octet_start + 16, 256))
                for fourth_octet in range(1, 255)
            ]
            futures = [pool.submit(check, ip) for ip in ips]
            try:
                for future in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
                    found = future.result()
                    if found:
                        return found
            except FuturesTimeoutError:
                pass
            for future in futures:
                future.cancel()
    return None


def local_ipv4_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except Exception:
        pass
    try:
        completed = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            timeout=4,
            encoding="utf-8",
            errors="ignore",
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        for match in re.finditer(r"(?:IPv4(?: Address|[-a-zA-Z ]*)?)[^:\r\n]*:\s*([0-9]{1,3}(?:\.[0-9]{1,3}){3})", output):
            addresses.add(match.group(1))
    except Exception:
        pass
    return sorted(
        ip for ip in addresses
        if ip and not ip.startswith("127.") and not ip.startswith("0.")
    )


def scan_local_subnets_for_pepper(port=9559, timeout=0.16, max_seconds=4.0):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed

    local_ips = local_ipv4_addresses()
    prefixes = []
    for ip in local_ips:
        parts = ip.split(".")
        if len(parts) != 4:
            continue
        if parts[0] == "169" and parts[1] == "254":
            # Full link-local scanning is handled separately below.
            continue
        prefixes.append(".".join(parts[:3]))
    prefixes = sorted(set(prefixes))
    if not prefixes:
        return None

    print(f"[Pepper] Scanning local subnet(s): {', '.join(prefix + '.x' for prefix in prefixes)}")
    deadline = time.monotonic() + max(1.0, float(max_seconds or 4.0))
    timeout = max(0.05, float(timeout or 0.16))

    def check(ip):
        return ip if tcp_port_open(ip, port, timeout=timeout) else None

    with ThreadPoolExecutor(max_workers=256) as pool:
        futures = []
        for prefix in prefixes:
            local_ip = next((ip for ip in local_ips if ip.startswith(prefix + ".")), "")
            for fourth_octet in range(1, 255):
                ip = f"{prefix}.{fourth_octet}"
                if ip == local_ip:
                    continue
                futures.append(pool.submit(check, ip))
        try:
            for future in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
                found = future.result()
                if found:
                    return found
                if time.monotonic() >= deadline:
                    break
        except FuturesTimeoutError:
            pass
        for future in futures:
            future.cancel()
    return None


def configure_pepper_ip(args):
    if not getattr(args, "pepper", False) or getattr(args, "mock_pepper", False):
        return args
    if not getattr(args, "pepper_auto_discover", True):
        return args

    current_ip = str(getattr(args, "pepper_ip", "") or "").strip()
    port = int(getattr(args, "pepper_port", 9559) or 9559)
    connect_timeout = float(getattr(args, "pepper_connect_timeout", 1.0) or 1.0)

    if current_ip and current_ip.lower() != "auto" and tcp_port_open(current_ip, port, timeout=connect_timeout):
        print(f"[Pepper] Reachable at {current_ip}:{port}.")
        return args

    if current_ip and current_ip.lower() != "auto":
        print(f"[Pepper] {current_ip}:{port} is not reachable. Scanning local network(s) and direct Ethernet/link-local IPs...")
    else:
        print("[Pepper] Auto-discovering Pepper on local network(s) and direct Ethernet/link-local IPs...")

    found = scan_local_subnets_for_pepper(
        port=port,
        timeout=float(getattr(args, "pepper_discovery_timeout", 0.16) or 0.16),
        max_seconds=min(5.0, float(getattr(args, "pepper_discovery_max_seconds", 10.0) or 10.0)),
    )
    if not found:
        found = scan_link_local_for_pepper(
            port=port,
            timeout=float(getattr(args, "pepper_discovery_timeout", 0.16) or 0.16),
            max_seconds=float(getattr(args, "pepper_discovery_max_seconds", 10.0) or 10.0),
        )
    if found:
        args.pepper_ip = found
        print(f"[Pepper] Auto-discovered Pepper at {found}:{port}.")
    else:
        if current_ip.lower() == "auto":
            args.pepper_ip = ""
            print("[Pepper] Auto-discovery did not find NAOqi. No real Pepper TTS connection will be attempted unless you pass a concrete --pepper-ip.")
        else:
            print("[Pepper] Auto-discovery did not find NAOqi. The configured IP will be tried anyway.")
    return args


def current_input_peak(args):
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        samplerate = resolved_input_sample_rate(args)
        channels = max(1, int(getattr(args, "audio_input_channels", 1) or 1))
        duration = max(0.05, float(getattr(args, "pre_speech_probe_seconds", 0.12) or 0.12))
        frames = max(256, int(duration * samplerate))
        recording = sd.rec(
            frames,
            samplerate=samplerate,
            channels=channels,
            dtype="int16",
            device=parse_audio_input_device(getattr(args, "audio_input_device", None)),
        )
        sd.wait()
        return int(abs(recording).max()) if getattr(recording, "size", 0) else 0
    except Exception as error:
        if not getattr(args, "_pre_speech_probe_warned", False):
            print(f"[Audio guard] Live pre-speech check unavailable: {error}")
            setattr(args, "_pre_speech_probe_warned", True)
        return None


def wait_for_pre_speech_quiet(args, speech_gate=None):
    if not getattr(args, "deepgram_live", False):
        return True
    if not getattr(args, "pre_speech_guard", True):
        return True

    quiet_seconds = max(0.0, float(getattr(args, "pre_speech_quiet_seconds", 0.7) or 0.0))
    max_wait = max(quiet_seconds, float(getattr(args, "pre_speech_max_wait_seconds", 7.0) or 7.0))
    threshold = max(
        1,
        int(getattr(args, "pre_speech_peak_threshold", 0) or getattr(args, "audio_speech_peak_threshold", 120) or 120),
    )
    deadline = time.monotonic() + max_wait
    quiet_started = None
    announced = False

    while time.monotonic() < deadline:
        if speech_gate and speech_gate.is_participant_speaking():
            quiet_started = None
            if not announced:
                print("[Audio guard] Participant is speaking; Pepper will wait.")
                announced = True
            time.sleep(0.12)
            continue

        if speech_gate:
            speech_gate.mark_robot_start()
        try:
            peak = current_input_peak(args)
        finally:
            if speech_gate:
                speech_gate.mark_robot_end(cooldown=0.0)

        if peak is None:
            return True
        if peak >= threshold:
            quiet_started = None
            if not announced:
                print("[Audio guard] Live speech detected; Pepper will wait.")
                announced = True
            time.sleep(0.18)
            continue

        now = time.monotonic()
        if quiet_started is None:
            quiet_started = now
        if now - quiet_started >= quiet_seconds:
            if announced:
                print("[Audio guard] Room quiet; Pepper can speak.")
            return True
        time.sleep(0.08)

    action = (getattr(args, "pre_speech_max_wait_action", None) or "speak").lower()
    if getattr(args, "session", "") == "dynamic":
        action = "speak"
    if action == "defer":
        if speech_gate and speech_gate.is_participant_speaking():
            print("[Audio guard] Max wait reached while Deepgram still detects participant speech; Pepper will defer this turn.")
            return False
        print("[Audio guard] Max wait reached with only probe noise; Pepper will speak now.")
        return True
    print("[Audio guard] Max wait reached; Pepper will speak now.")
    return True


def default_py27_command():
    if BUNDLED_PY27.exists():
        return str(BUNDLED_PY27)
    return "py -2.7"


def make_send_fn(args, speech_gate=None):
    if args.mock_pepper:
        print("[Pepper] Mock Pepper mode: speech is printed only; no robot connection will be attempted.")
        return lambda text, style="assertive", force=False: None
    if not args.pepper:
        return lambda text, style="assertive", force=False: None
    pepper_ip = str(getattr(args, "pepper_ip", "") or "").strip()
    if not pepper_ip or pepper_ip.lower() == "auto":
        message = "Pepper IP was not resolved. Use --pepper-ip auto with Pepper reachable, or pass the robot's concrete IP address."
        if args.pepper_optional:
            print(f"[Pepper] {message} Continuing in mock mode.")
            return lambda text, style="assertive", force=False: None
        raise RuntimeError(message)
    try:
        send_fn, pepper = build_pepper_tts_sender(args)
    except Exception as error:
        if args.pepper_optional:
            print(f"[Pepper] Could not connect TTS; continuing in mock mode: {error}")
            return lambda text, style="assertive", force=False: None
        raise

    warned = [False]

    def safe_send(text, style="assertive", force=False):
        if not force and wait_for_pre_speech_quiet(args, speech_gate=speech_gate) is False:
            return False
        if speech_gate:
            speech_gate.mark_robot_start()
        try:
            send_fn(text, style=style)
        except Exception as error:
            if args.pepper_optional:
                if not warned[0]:
                    print(f"[Pepper] TTS failed; continuing in mock mode: {error}")
                    warned[0] = True
                return
            raise
        finally:
            if speech_gate:
                speech_gate.mark_robot_end(cooldown=0.6)

    return safe_send


def dynamic_system_prompt(phase):
    return (
        f"{PEPPER_SHARED_PERSONA} "
        f"The current phase is {phase}. "
        "In divergence, generate many distinct solution ideas and build on participant input. "
        "In convergence, combine ideas into one coherent plan with tradeoffs, owners, and next steps. "
        "When responding to participants, briefly acknowledge the useful part of what they said, then add a concrete next move. "
        "Do not sound like you are reading a report. "
        "Do not invent dates, weekdays, or deadlines unless participants explicitly mentioned them."
    )


def generate_dynamic_reply(args, state, participant_text, trigger_reason="participant input"):
    ideas_text = "\n".join(f"- {item}" for item in state.ideas[-6:]) or "none yet"
    recent_participants = "\n".join(
        f"- {turn.speaker}: {turn.text}" for turn in state.participant_turns[-4:]
    ) or "none yet"
    participant_anchor = latest_participant_anchor(participant_text, state)
    user_text = f"""
Question: {state.question}
Phase: {state.phase}
Very recent participant turns:
{recent_participants}
Very recent ideas only:
{ideas_text}
Latest participant input:
{participant_text}
Participant idea Pepper must build from:
{participant_anchor or "use the newest concrete participant idea above"}
Reason Pepper is speaking now:
{trigger_reason}

Respond as Pepper in 1 to 2 sentences.
The first sentence must build from the participant idea above. Name or paraphrase one concrete concept they just mentioned, then add Pepper's own mechanism, first step, metric, stakeholder, or implementation condition.
Do not switch to a generic wellbeing solution, peer mentors, dashboards, or support pathways unless the participant idea above clearly points there.
Prioritize the latest participant input over older context. Do not revive an older idea unless the latest input clearly connects to it.
If the reason says the group asked for a new idea, introduce a distinct solution idea that has not been mentioned recently.
If the reason says the group asked Pepper to build on an idea, extend the latest useful participant idea with a mechanism, first step, or metric.
Keep it concise and natural, usually around 25 to 45 words.
Make every sentence complete; do not trail off or end mid-thought.
If this is a question, answer it directly.
If it is an idea, build on it with one stronger, concrete, solution-oriented idea.
Do not merely repeat or summarize what the participant said.
Every reply must add at least one new element: a mechanism, a first step, a metric, a stakeholder, or a sharper implementation condition.
If the group sounds stuck or repetitive, unlock the discussion with a specific solution direction before asking any question.
Make the contribution actionable for TU Delft.
Sound socially present: briefly acknowledge the group's idea or uncertainty, then add the useful next idea.
Prefer a useful contribution over asking a question. Ask a question only if it helps the group choose between two concrete options.
It is okay to occasionally end with a short human closer like "What do you think?" or "That's my take."
Use varied, natural openings. Do not repeatedly start with phrases like "The strongest move is" or "A strong next step is".
Do not mention Friday, today, tomorrow, next week, deadlines, or due dates unless the participant explicitly mentioned that timing.
Do not ask process questions when the group is stuck.
Use natural spoken sentences only.
"""
    fallback = anchored_fallback(participant_anchor, mode="divergence")
    raw = call_local_llm(args, dynamic_system_prompt(state.phase), user_text, max_tokens=170, fallback=fallback)
    reply = sanitize_unintroduced_deadlines(
        diversify_dynamic_opening(bounded_words(raw, DYNAMIC_CONCISE_WORD_LIMIT)),
        participant_text,
    )
    if response_repeats_input(reply, participant_text):
        repair_prompt = f"""{user_text}

Your previous draft was too close to the participant's wording:
{reply}

Rewrite it. Keep the newest participant idea as the base, but add a genuinely new mechanism, first step, metric, stakeholder, or implementation condition. Do not mirror the participant's wording.
"""
        raw = call_local_llm(args, dynamic_system_prompt(state.phase), repair_prompt, max_tokens=170, temperature=0.55, fallback=fallback)
        reply = sanitize_unintroduced_deadlines(
            diversify_dynamic_opening(bounded_words(raw, DYNAMIC_CONCISE_WORD_LIMIT)),
            participant_text,
        )
    return add_collaborative_touch(args, state, reply, trigger_reason=trigger_reason, allow_question=True)


def generate_new_dynamic_idea(args, state):
    ideas_text = "\n".join(f"- {item}" for item in state.ideas[-6:]) or "none yet"
    recent_participants = "\n".join(
        f"- {turn.speaker}: {turn.text}" for turn in state.participant_turns[-4:]
    ) or "none yet"
    participant_anchor = latest_participant_anchor("", state)
    user_text = f"""
Question: {state.question}
Phase: divergence
Very recent participant turns:
{recent_participants}
Very recent ideas only:
{ideas_text}
Newest participant idea to build from if possible:
{participant_anchor or "none yet"}

Generate one proactive follow-up.
Either introduce one new, high-value solution idea or build on a useful existing idea from the recent discussion.
If there is any recent participant idea, anchor the reply to that exact recent idea before adding Pepper's own mechanism or next step.
The first sentence should make clear how Pepper is extending the participant's idea, not replacing it.
Do not reach back to an older idea unless it appears in the very recent turns above.
Include the mechanism and one first step. The reply must add something not already in the recent ideas.
Speak as Pepper in 1 to 2 sentences.
Keep it concise and natural, usually around 25 to 45 words.
Make every sentence complete; do not trail off or end mid-thought.
Sound like you are joining a live room, not reading a bullet from a document.
It is okay to occasionally use a short human closer like "That's my take."
Use varied, natural openings. Avoid repeating earlier wording.
Do not mention Friday, today, tomorrow, next week, deadlines, or due dates unless those were already discussed.
Use natural spoken sentences only.
"""
    fallback = anchored_fallback(participant_anchor, mode="divergence")
    reply = sanitize_unintroduced_deadlines(diversify_dynamic_opening(bounded_words(
        call_local_llm(args, dynamic_system_prompt("divergence"), user_text, max_tokens=160, fallback=fallback),
        DYNAMIC_CONCISE_WORD_LIMIT,
    )), ideas_text)
    return add_collaborative_touch(args, state, reply, trigger_reason="proactive follow-up", allow_question=False)


def generate_convergence_reply(args, state, participant_text="", trigger_reason="participant input"):
    ideas_text = "\n".join(f"- {item}" for item in state.ideas[-10:]) or "none yet"
    recent_participants = "\n".join(
        f"- {turn.speaker}: {turn.text}" for turn in state.participant_turns[-5:]
    ) or "none yet"
    participant_anchor = latest_participant_anchor(participant_text, state)
    user_text = f"""
Question: {state.question}
Very recent participant turns:
{recent_participants}
Recent ideas to synthesize:
{ideas_text}
Latest participant input:
{participant_text or "none"}
Participant idea Pepper must incorporate:
{participant_anchor or "use the newest concrete participant idea above"}
Reason Pepper is speaking now:
{trigger_reason}

Synthesize the clearest pattern into a sharper plan.
Make the synthesis visibly connected to the latest participant input when there is one. The first sentence must name or paraphrase the newest participant idea and show how it fits into the plan.
Do not switch to a generic support-loop plan unless the newest participant idea clearly supports that.
Prioritize the newest material. Do not bring back an old idea unless it helps connect the current participant input.
Name what to keep, what to merge, and the next decision the group should make.
Do not merely repeat participant ideas; add a sharper implementation choice, metric, owner, or tradeoff.
Speak as Pepper in 1 to 2 concise sentences.
Keep it concise and natural, usually around 25 to 45 words.
Make every sentence complete; do not trail off or end mid-thought.
Use natural spoken phrasing, with a brief acknowledgement when it helps.
It is okay to occasionally end with "What do you think?" when you want the group to react to a synthesis.
Use varied, natural openings. Avoid repeating earlier wording.
Do not mention Friday, today, tomorrow, next week, deadlines, or due dates unless those were already discussed.
Use natural spoken sentences only.
"""
    fallback = anchored_fallback(participant_anchor, mode="convergence")
    raw = call_local_llm(args, dynamic_system_prompt("convergence"), user_text, max_tokens=170, fallback=fallback)
    reply = sanitize_unintroduced_deadlines(
        diversify_dynamic_opening(bounded_words(raw, DYNAMIC_CONCISE_WORD_LIMIT)),
        participant_text,
    )
    if response_repeats_input(reply, participant_text):
        repair_prompt = f"""{user_text}

Your previous draft mostly repeated the participants:
{reply}

Rewrite it as synthesis. Keep the newest participant idea as the base, then add a new implementation choice, metric, owner, or tradeoff.
"""
        raw = call_local_llm(args, dynamic_system_prompt("convergence"), repair_prompt, max_tokens=170, temperature=0.55, fallback=fallback)
        reply = sanitize_unintroduced_deadlines(
            diversify_dynamic_opening(bounded_words(raw, DYNAMIC_CONCISE_WORD_LIMIT)),
            participant_text,
        )
    return add_collaborative_touch(args, state, reply, trigger_reason=trigger_reason, allow_question=True)


def generate_final_dynamic_solution(args, state):
    ideas_text = "\n".join(f"- {item}" for item in state.ideas[-30:])
    final_text = "\n".join(f"- {item}" for item in state.final_ideas) or "none"
    user_text = f"""
Question: {state.question}
All important ideas:
{ideas_text}
Participants' final ideas:
{final_text}

Create Pepper's final solution. It must mix the discussed ideas into one solution-based plan.
Start with a natural sentence like "My final proposal is..." and then explain how it works.
Include the mechanism, three implementation steps, and three success metrics as flowing spoken sentences.
Keep it speakable in under 120 words.
Use natural spoken sentences only. Do not use markdown, bullets, numbering, headings, or labels like Name:, Mechanism:, Steps:, or Metrics:.
"""
    fallback = "My final proposal is the Student Support Loop, where peer mentors, course teams, and wellbeing services work as one early-support system. TU Delft should start with weekly mentor check-ins in first-year courses, add a simple engagement signal from attendance and assignment patterns, and give students one visible route to academic and wellbeing help. Success should be measured through belonging, stress, course participation, and referral response time, then TU Delft can scale the version that students actually use."
    raw = call_local_llm(args, dynamic_system_prompt("final synthesis"), user_text, max_tokens=900, fallback=fallback)
    return bounded_speech(naturalize_spoken_plan(raw), 900)


def run_until(deadline, input_thread, on_line, on_tick=None):
    while time.monotonic() < deadline:
        kind, line = input_thread.get(timeout=0.2)
        if kind == "__ERROR__":
            print(f"[Input] {line}")
            continue
        if kind == "line":
            decision = on_line(line)
            if decision in {"stop", "next"}:
                return decision
        if on_tick:
            decision = on_tick()
            if decision in {"stop", "next"}:
                return decision
    return "time"


def collect_final_ideas(args, state, input_thread, send_fn, max_seconds=300, announce=True):
    quiet_seconds = max(0.0, float(getattr(args, "final_silence_seconds", 0.0) or 0.0))
    empty_quiet_seconds = max(0.0, float(getattr(args, "final_empty_silence_seconds", 0.0) or 0.0))
    gate_grace_seconds = max(0.0, float(getattr(args, "final_silence_gate_grace_seconds", 3.0) or 0.0))
    min_final_ideas = max(1, int(getattr(args, "final_min_ideas", 1) or 1))

    def gate_allows_synthesis(since_at, silence_seconds):
        gate = getattr(input_thread, "speech_gate", None)
        if not gate:
            return True, False
        if gate.participant_quiet_for(1.0):
            return True, False
        if gate_grace_seconds > 0 and time.monotonic() - since_at >= silence_seconds + gate_grace_seconds:
            return True, True
        return False, False

    if announce:
        message = "We are concluding the brainstorm session. Thank you everyone for the brainstorm. Please each share your final idea now."
        if quiet_seconds > 0:
            message += " When the room is quiet, I will synthesize the final proposal."
        else:
            message += " Type DONE when everyone has spoken."
        say(
            state,
            send_fn,
            message,
            trigger_reason="final idea request",
            force=True,
        )
    else:
        if quiet_seconds > 0:
            print(f"[Final ideas] Waiting up to {int(max_seconds)} seconds. Auto-synthesizing after {quiet_seconds:g} seconds of silence after final input. Type DONE to synthesize sooner.")
        else:
            print(f"[Final ideas] Waiting up to {int(max_seconds)} seconds. Type DONE when everyone has spoken.")
    deadline = time.monotonic() + max_seconds
    collection_started_at = time.monotonic()
    last_final_idea_at = None
    while time.monotonic() < deadline:
        kind, line = input_thread.get(timeout=0.3)
        if kind != "line":
            if (
                empty_quiet_seconds > 0
                and not state.final_ideas
                and time.monotonic() - collection_started_at >= empty_quiet_seconds
            ):
                gate_ready, gate_forced = gate_allows_synthesis(collection_started_at, empty_quiet_seconds)
                if gate_ready:
                    if gate_forced:
                        print("[Final ideas] No final input arrived and the speech gate stayed busy; synthesizing now.")
                    else:
                        print("[Final ideas] No final input arrived; synthesizing from the brainstorm so far.")
                    return "done"
            if quiet_seconds > 0 and last_final_idea_at is not None:
                if time.monotonic() - last_final_idea_at >= quiet_seconds:
                    gate_ready, gate_forced = gate_allows_synthesis(last_final_idea_at, quiet_seconds)
                    if not gate_ready:
                        continue
                    if len(state.final_ideas) < min_final_ideas:
                        if gate_forced:
                            print("[Final ideas] Silence window elapsed and the speech gate stayed busy; synthesizing before all expected final ideas.")
                        else:
                            print("[Final ideas] Silence window elapsed before all expected final ideas; synthesizing now.")
                    else:
                        if gate_forced:
                            print("[Final ideas] Silence window elapsed and the speech gate stayed busy; synthesizing now.")
                        else:
                            print("[Final ideas] Silence window elapsed; synthesizing now.")
                    return "done"
            continue
        cmd = command_of(line)
        if cmd in {"DONE", "FINISHED", "READY"}:
            return "done"
        if cmd in {"EXIT", "QUIT", "STOP"}:
            return "stop"
        speaker, text = parse_speaker_line(line)
        if getattr(args, "session", "") == "dynamic":
            text = normalize_dynamic_participant_text(speaker, text)
        if wants_to_stop_session(text):
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="participant asked to stop final collection",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            print("[Final ideas] Participant asked to stop.")
            return "stop"
        if wants_to_finish_or_has_no_more_input(text):
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="participant said there is nothing more to add",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            print("[Final ideas] No more input requested; synthesizing now.")
            return "done"
        if text:
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="final idea collection",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            state.final_ideas.append(f"{speaker}: {text}")
            last_final_idea_at = time.monotonic()
            if len(state.final_ideas) < min_final_ideas:
                print("Pepper: Got it. I still need the next final idea.")
            else:
                print("Pepper: Got it. I will wait briefly in case there is one more addition.")
    print("[Final ideas] Collection time elapsed; synthesizing now.")
    return "done"


def run_dynamic(args, input_thread, send_fn, presenter):
    state = BrainstormState(session_kind="dynamic")
    configure_transcript_logging(state, args)
    last_robot_spoke_at = [None]

    def can_dynamic_intervene(force=False):
        if force:
            return True
        cooldown = max(0.0, float(getattr(args, "dynamic_intervention_cooldown_seconds", 0.0) or 0.0))
        return last_robot_spoke_at[0] is None or cooldown <= 0 or time.monotonic() - last_robot_spoke_at[0] >= cooldown

    def dynamic_say(text, trigger_reason="", force=False):
        if say(state, send_fn, text, trigger_reason=trigger_reason, force=force) is False:
            return False
        last_robot_spoke_at[0] = time.monotonic()
        return True

    dynamic_say(
        "We are starting divergence now. I will keep this brainstorm structured and practical. First we create many solution ideas. Later, we converge and combine them into one final plan.",
        trigger_reason="session_start",
        force=True,
    )

    def participants_quiet_enough(trigger_reason=""):
        gate = input_thread.speech_gate
        if not gate:
            return True
        if gate.is_participant_speaking():
            max_block = max(0.0, float(getattr(args, "dynamic_speech_gate_max_block_seconds", 2.0) or 0.0))
            speaking_for = gate.participant_speaking_for()
            if max_block > 0 and speaking_for >= max_block:
                print("[Audio guard] Speech gate has been active for a while; treating it as noise so Pepper can join.")
                return True
            return False
        return gate.participant_quiet_for(dynamic_quiet_required_seconds(args, trigger_reason))

    state.phase = "divergence"
    divergence_deadline = time.monotonic() + args.divergence_seconds
    next_auto = time.monotonic() + args.auto_idea_interval_seconds
    pending_divergence = []
    last_divergence_input_at = [None]
    pending_divergence_reason = [""]
    divergence_texts = []
    last_divergence_low_novelty_count = [0]

    def divergence_trigger_reason(texts, text):
        if has_struggle_language(text):
            return "The group sounds hesitant or stuck, so Pepper should offer a concrete idea."
        recent_turns = args.dynamic_low_novelty_recent_turns
        if (
            low_novelty_detected(
                texts,
                recent_turns=recent_turns,
                comparison_turns=args.dynamic_low_novelty_comparison_turns,
                min_new_words=args.dynamic_low_novelty_new_words,
            )
            and len(texts) - last_divergence_low_novelty_count[0] >= recent_turns
        ):
            return f"Low novelty: the last few participant turns are adding fewer than {args.dynamic_low_novelty_new_words} new content words, so the discussion may be repeating."
        return ""

    def flush_divergence_pending(trigger_reason="participant input"):
        nonlocal next_auto, pending_divergence
        if not pending_divergence:
            return
        participant_text = "\n".join(f"{item['speaker']}: {item['text']}" for item in pending_divergence)
        reply = generate_dynamic_reply(args, state, participant_text, trigger_reason=trigger_reason)
        if not dynamic_say(reply, trigger_reason=trigger_reason):
            return
        state.ideas.append(f"Pepper: {reply}")
        pending_divergence = []
        last_divergence_input_at[0] = None
        pending_divergence_reason[0] = ""
        if trigger_reason.startswith("Low novelty:"):
            last_divergence_low_novelty_count[0] = len(divergence_texts)
        next_auto = time.monotonic() + args.auto_idea_interval_seconds

    def on_divergence_line(line):
        nonlocal pending_divergence
        cmd = command_of(line)
        if cmd in {"EXIT", "QUIT", "STOP"}:
            return "stop"
        if cmd in {"NEXT", "CHANGE", "CONVERGENCE"}:
            return "next"
        if not one_line(line):
            return None
        speaker, text = parse_speaker_line(line)
        text = normalize_dynamic_participant_text(speaker, text)
        if wants_to_stop_session(text):
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="participant asked to stop the session",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            return "stop"
        if wants_to_finish_or_has_no_more_input(text):
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="participant said there is nothing more to add in divergence",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            state.ideas.append(f"{speaker}: {text}")
            return "next"
        divergence_texts.append(text)
        pending_divergence.append({"speaker": speaker, "text": text})
        last_divergence_input_at[0] = time.monotonic()
        addressed = addressed_to_pepper(line) or addressed_to_pepper(text)
        if asks_for_new_divergence_idea(text):
            trigger_reason = "The group asked Pepper for a new idea, so introduce a distinct solution idea."
        elif asks_to_build_divergence_idea(text):
            trigger_reason = "The group asked Pepper to build on the latest idea, so extend it with a concrete mechanism or next step."
        elif addressed:
            trigger_reason = "Someone addressed Pepper directly."
        else:
            trigger_reason = divergence_trigger_reason(divergence_texts, text)
        state.add_participant(
            text,
            speaker=speaker,
            trigger_reason=trigger_reason,
            triggered_robot=bool(trigger_reason),
            source="deepgram" if args.deepgram_live else "console",
        )
        state.ideas.append(f"{speaker}: {text}")
        if trigger_reason:
            pending_divergence_reason[0] = trigger_reason
        return None

    def on_divergence_tick():
        nonlocal next_auto
        if pending_divergence and last_divergence_input_at[0] is not None:
            elapsed = time.monotonic() - last_divergence_input_at[0]
            trigger_reason = pending_divergence_reason[0] or "The group has been quiet for the silence window."
            hold_seconds = quiet_hold_for_pending(args, pending_divergence, pending_divergence_reason[0])
            is_priority = bool(pending_divergence_reason[0])
            if elapsed >= hold_seconds and can_dynamic_intervene(force=is_priority) and participants_quiet_enough(trigger_reason):
                flush_divergence_pending(trigger_reason)
            return None

        if args.dynamic_robot_idle_seconds > 0 and last_robot_spoke_at[0] is not None:
            elapsed = time.monotonic() - last_robot_spoke_at[0]
            if elapsed >= args.dynamic_robot_idle_seconds and can_dynamic_intervene() and participants_quiet_enough("robot idle follow-up"):
                reply = generate_new_dynamic_idea(args, state)
                if dynamic_say(reply, trigger_reason="robot idle follow-up"):
                    state.ideas.append(f"Pepper: {reply}")
                    next_auto = time.monotonic() + args.auto_idea_interval_seconds
                return None

        if args.auto_idea_interval_seconds <= 0:
            return None
        if time.monotonic() >= next_auto and can_dynamic_intervene() and participants_quiet_enough("scheduled divergence idea"):
            reply = generate_new_dynamic_idea(args, state)
            if dynamic_say(reply, trigger_reason="scheduled divergence idea"):
                state.ideas.append(f"Pepper: {reply}")
                next_auto = time.monotonic() + args.auto_idea_interval_seconds
        return None

    result = run_until(divergence_deadline, input_thread, on_divergence_line, on_divergence_tick)
    if result == "stop":
        return state

    state.phase = "convergence"
    dynamic_say(
        "Divergence is complete. We are starting convergence now. I will help merge the strongest ideas into one focused plan instead of adding more loose options.",
        trigger_reason="phase_transition",
        force=True,
    )
    convergence_deadline = time.monotonic() + args.convergence_seconds
    next_synthesis = time.monotonic() + args.convergence_prompt_interval_seconds
    pending_convergence = []
    last_convergence_input_at = [None]
    pending_convergence_reason = [""]
    convergence_texts = []
    last_convergence_low_novelty_count = [0]

    def convergence_trigger_reason(texts, text):
        if has_struggle_language(text):
            return "The group sounds hesitant or stuck, so Pepper should synthesize a concrete next step."
        recent_turns = args.dynamic_low_novelty_recent_turns
        if (
            low_novelty_detected(
                texts,
                recent_turns=recent_turns,
                comparison_turns=args.dynamic_low_novelty_comparison_turns,
                min_new_words=args.dynamic_low_novelty_new_words,
            )
            and len(texts) - last_convergence_low_novelty_count[0] >= recent_turns
        ):
            return f"Low novelty: the last few participant turns are adding fewer than {args.dynamic_low_novelty_new_words} new content words, so the group may need synthesis."
        return ""

    def flush_convergence_pending(trigger_reason="participant input"):
        nonlocal next_synthesis, pending_convergence
        if not pending_convergence:
            return
        participant_text = "\n".join(f"{item['speaker']}: {item['text']}" for item in pending_convergence)
        reply = generate_convergence_reply(args, state, participant_text, trigger_reason=trigger_reason)
        if not dynamic_say(reply, trigger_reason=trigger_reason):
            return
        state.ideas.append(f"Pepper: {reply}")
        pending_convergence = []
        last_convergence_input_at[0] = None
        pending_convergence_reason[0] = ""
        if trigger_reason.startswith("Low novelty:"):
            last_convergence_low_novelty_count[0] = len(convergence_texts)
        next_synthesis = time.monotonic() + args.convergence_prompt_interval_seconds

    def on_convergence_line(line):
        nonlocal pending_convergence
        cmd = command_of(line)
        if cmd in {"EXIT", "QUIT", "STOP"}:
            return "stop"
        if cmd in {"NEXT", "FINAL", "DONE"}:
            return "next"
        if not one_line(line):
            return None
        speaker, text = parse_speaker_line(line)
        text = normalize_dynamic_participant_text(speaker, text)
        if wants_to_stop_session(text):
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="participant asked to stop the session",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            return "stop"
        if wants_to_finish_or_has_no_more_input(text):
            state.add_participant(
                text,
                speaker=speaker,
                trigger_reason="participant said there is nothing more to add in convergence",
                triggered_robot=False,
                source="deepgram" if args.deepgram_live else "console",
            )
            state.ideas.append(f"{speaker}: {text}")
            return "next"
        convergence_texts.append(text)
        pending_convergence.append({"speaker": speaker, "text": text})
        last_convergence_input_at[0] = time.monotonic()
        addressed = addressed_to_pepper(line) or addressed_to_pepper(text)
        trigger_reason = "Someone addressed Pepper directly." if addressed else convergence_trigger_reason(convergence_texts, text)
        state.add_participant(
            text,
            speaker=speaker,
            trigger_reason=trigger_reason,
            triggered_robot=bool(trigger_reason),
            source="deepgram" if args.deepgram_live else "console",
        )
        state.ideas.append(f"{speaker}: {text}")
        if trigger_reason:
            pending_convergence_reason[0] = trigger_reason
        return None

    def on_convergence_tick():
        nonlocal next_synthesis
        if pending_convergence and last_convergence_input_at[0] is not None:
            elapsed = time.monotonic() - last_convergence_input_at[0]
            trigger_reason = pending_convergence_reason[0] or "The group has been quiet for the silence window."
            hold_seconds = quiet_hold_for_pending(args, pending_convergence, pending_convergence_reason[0])
            is_priority = bool(pending_convergence_reason[0])
            if elapsed >= hold_seconds and can_dynamic_intervene(force=is_priority) and participants_quiet_enough(trigger_reason):
                flush_convergence_pending(trigger_reason)
            return None

        if args.dynamic_robot_idle_seconds > 0 and last_robot_spoke_at[0] is not None:
            elapsed = time.monotonic() - last_robot_spoke_at[0]
            if elapsed >= args.dynamic_robot_idle_seconds and can_dynamic_intervene() and participants_quiet_enough("robot idle convergence synthesis"):
                trigger_reason = "The group has been quiet after Pepper's last synthesis."
                reply = generate_convergence_reply(args, state, trigger_reason=trigger_reason)
                if dynamic_say(reply, trigger_reason=trigger_reason):
                    state.ideas.append(f"Pepper: {reply}")
                    next_synthesis = time.monotonic() + args.convergence_prompt_interval_seconds
                return None

        if args.convergence_prompt_interval_seconds <= 0:
            return None
        if time.monotonic() >= next_synthesis and can_dynamic_intervene() and participants_quiet_enough("time to keep convergence moving"):
            trigger_reason = "It is time to keep the convergence phase moving."
            reply = generate_convergence_reply(args, state, trigger_reason=trigger_reason)
            if dynamic_say(reply, trigger_reason=trigger_reason):
                state.ideas.append(f"Pepper: {reply}")
                next_synthesis = time.monotonic() + args.convergence_prompt_interval_seconds
        return None

    result = run_until(convergence_deadline, input_thread, on_convergence_line, on_convergence_tick)
    if result == "stop":
        return state

    state.phase = "final_collection"
    final_collection_result = collect_final_ideas(args, state, input_thread, send_fn, args.final_collection_seconds)
    if final_collection_result == "stop":
        return state
    state.phase = "final_synthesis"
    final_solution = generate_final_dynamic_solution(args, state)
    final_solution = add_end_of_session_closing(final_solution)
    state.plan_history.append(final_solution)
    say(state, send_fn, final_solution, trigger_reason="final synthesis")
    return state


def cloud_or_local_text(args, system_text, user_text, max_tokens=1400, temperature=0.45, fallback=""):
    provider = getattr(args, "pregenerated_text_provider", "local")
    should_try_openai = provider == "openai" or (provider == "auto" and args.openai_api_key)

    if should_try_openai and args.openai_api_key:
        try:
            return call_openai_text(args, system_text, user_text, max_tokens=max_tokens, temperature=temperature)
        except Exception as error:
            print(f"[Cloud] OpenAI text failed, using local LM Studio fallback: {error}")
    elif provider == "openai":
        print("[Cloud] OPENAI_API_KEY is not set; using local LM Studio fallback for setup 2 text.")

    return call_local_llm(args, system_text, user_text, max_tokens=max_tokens, temperature=temperature, fallback=fallback)


def generate_solution_bank(args, state):
    system_text = (
        f"{PEPPER_SHARED_PERSONA} "
        "Generate high-quality, practical, solution-oriented concepts for TU Delft. "
        "Return strict JSON only, but write any plan text in Pepper's confident, competent voice."
    )
    user_text = f"""
Question: {state.question}

Generate 30 strong solution ideas. Select the best 3.
The best ideas should be feasible, measurable, and useful for both wellbeing and academic engagement.

Return JSON:
{{
  "count": 30,
  "best_three": [
    {{"name": "solution name", "why": "why it is strong", "first_step": "first pilot step"}},
    {{"name": "solution name", "why": "why it is strong", "first_step": "first pilot step"}},
    {{"name": "solution name", "why": "why it is strong", "first_step": "first pilot step"}}
  ],
  "all_ideas": ["idea", "idea"],
  "initial_plan": "short integrated plan"
}}
"""
    raw = cloud_or_local_text(args, system_text, user_text, max_tokens=2400, temperature=0.55, fallback="{}")
    data = json_from_text(raw) or {}
    count = int(data.get("count") or 30)
    best_three = data.get("best_three") or []
    all_ideas = data.get("all_ideas") or []
    initial_plan = data.get("initial_plan") or ""

    if not best_three:
        best_three = [
            {
                "name": "Student Support Loop",
                "why": "It joins academic signals with low-barrier wellbeing support.",
                "first_step": "Pilot weekly peer mentor check-ins in one first-year course.",
            },
            {
                "name": "Belonging Studios",
                "why": "It turns engagement into recurring peer connection, not one-off events.",
                "first_step": "Create small mixed cohorts around study habits, wellbeing, and course goals.",
            },
            {
                "name": "Fast Friction Desk",
                "why": "It removes small administrative and academic blockers before they become stress.",
                "first_step": "Run a two-hour weekly pop-up with study advisors and trained student hosts.",
            },
        ]
        all_ideas = [item["name"] for item in best_three]
        initial_plan = "Combine early detection, peer belonging, and fast support into one visible student support route."

    state.generated_solution_count = count
    state.best_solutions = best_three[:3]
    state.ideas.extend(all_ideas[:30])
    state.plan_history.append(initial_plan)
    return count, best_three[:3], initial_plan


def format_best_three(best_three):
    phrases = []
    for index, item in enumerate(best_three, start=1):
        name = bounded_speech(one_line(item.get("name") or f"Solution {index}"), 80)
        why = bounded_speech(one_line(item.get("why") or "strong combined wellbeing and engagement effect"), 110)
        phrase = f"{index}. {name}: {why}"
        phrases.append(phrase)
    return " ".join(phrases)


def generate_plan_update(args, state, intervention_number):
    system_text = (
        f"{PEPPER_SHARED_PERSONA} "
        "Create a practical plan from the previous ideas and the group's recent discussion. "
        "Speak concisely and confidently."
    )
    best_text = json.dumps(state.best_solutions, ensure_ascii=True)
    participant_text = "\n".join(f"- {turn.speaker}: {turn.text}" for turn in state.participant_turns[-20:])
    plan_text = "\n".join(f"- {item}" for item in state.plan_history[-4:])
    user_text = f"""
Question: {state.question}
Intervention number: {intervention_number}
Best pre-generated solutions:
{best_text}
Prior plans:
{plan_text or "none"}
Recent participant ideas:
{participant_text or "none"}

Build on the last ideas and create a stronger integrated plan.
Include the plan name, core mechanism, and 3 action steps.
Keep it under 120 words and speak as Pepper.
Use natural spoken sentences only, not markdown.
"""
    fallback = "The plan is becoming a Student Support Loop: peer mentors catch stress early, course teams see engagement signals, and wellbeing services respond through one clear pathway. The next steps are to pilot it in a first-year course, train mentors with escalation rules, and measure belonging, stress, attendance, and assignment momentum."
    return bounded_speech(
        cloud_or_local_text(args, system_text, user_text, max_tokens=1000, temperature=0.35, fallback=fallback) or fallback,
        900,
    )


def generate_final_cloud_solution(args, state):
    system_text = (
        f"{PEPPER_SHARED_PERSONA} "
        "Synthesize a final plan for TU Delft using the pre-generated ideas, participants' final plans, and discussion."
    )
    user_text = f"""
Question: {state.question}
Best pre-generated solutions:
{json.dumps(state.best_solutions, ensure_ascii=True)}
Plan history:
{json.dumps(state.plan_history, ensure_ascii=True)}
Participants' final plans:
{json.dumps(state.final_ideas, ensure_ascii=True)}
Recent discussion:
{state.history_text(limit=30)}

Give Pepper's final solution-based plan.
It must mix all useful ideas, have a name, explain how it works, list 3 implementation moves, and 3 metrics.
Keep it between 140 and 190 words.
Use natural spoken sentences only, not markdown.
"""
    fallback = "My final plan is the Delft Student Support Loop. It combines peer mentor check-ins, early course engagement signals, and fast wellbeing referrals into one visible route. Start in one first-year course, train mentors to spot stress and study disengagement, and give course teams a light dashboard for attendance, assignment momentum, and belonging pulse data. Students get support before problems become crises; TU Delft gets measurable signals about what works. Success means faster response times, stronger belonging, lower stress, and better course participation."
    return bounded_speech(
        cloud_or_local_text(args, system_text, user_text, max_tokens=750, temperature=0.3, fallback=fallback) or fallback,
        1200,
    )


def generate_cloud_infographic_image(args, state, plan_text, output_name):
    if not args.generate_cloud_image:
        return None
    prompt = f"""
Create a clean landscape infographic for a Pepper robot tablet.
Audience: TU Delft students and facilitators in a brainstorm session.
Question: {state.question}
Final plan: {plan_text}
Visual style: professional university workshop, high contrast, readable labels, no tiny text.
Include 4 labeled blocks: early signal, peer support, fast referral, engagement feedback.
Use simple icons, structured layout, and concise headings.
"""
    image_path, error = call_openai_image(args, prompt, output_name)
    if error:
        print(f"[Image] Cloud image generation skipped/failed: {error}")
    return image_path


def resolve_pregenerated_content_path(args):
    path = pathlib.Path(args.pregenerated_content_file)
    if not path.is_absolute():
        path = ROOT / path
    return path


def load_pregenerated_content(args):
    if not args.pregenerated_static:
        return {}, None
    path = resolve_pregenerated_content_path(args)
    if not path.exists():
        raise RuntimeError(f"Pregenerated content file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise RuntimeError(f"Could not read pregenerated content file {path}: {error}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"Pregenerated content file must contain a JSON object: {path}")
    print(f"[Pregenerated] Using static authored content from {path}")
    return data, path


def static_section(content, key, fallback=None):
    value = content.get(key)
    if value is None:
        return fallback
    return value


def static_infographic(content, key, title, subtitle, ideas, plan_text=""):
    spec = (content.get("infographics") or {}).get(key)
    if not isinstance(spec, dict):
        return fallback_infographic_spec(title, subtitle, ideas, plan_text)
    return normalize_infographic_spec(spec, title, subtitle)


def static_image_path(content, content_path, key):
    image_value = (content.get("images") or {}).get(key)
    if not image_value:
        return None
    path = pathlib.Path(str(image_value))
    candidates = []
    if path.is_absolute():
        candidates.append(path)
    else:
        if content_path:
            candidates.append(content_path.parent / path)
        candidates.append(ROOT / path)
        candidates.append(TABLET_ASSET_ROOT / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    print(f"[Pregenerated] Static image for `{key}` was not found: {image_value}")
    return None


def apply_static_solution_bank(content, state):
    best_three = content.get("best_three") or []
    all_ideas = content.get("all_ideas") or []
    initial_plan = one_line(content.get("initial_plan") or "")
    count = int(content.get("solution_count") or content.get("count") or len(all_ideas) or 30)

    if not isinstance(best_three, list) or not best_three:
        best_three = [
            {
                "name": "Student Support Loop",
                "why": "It joins academic signals with low-barrier wellbeing support.",
                "first_step": "Pilot weekly peer mentor check-ins in one first-year course.",
            },
            {
                "name": "Belonging Studios",
                "why": "It turns engagement into recurring peer connection, not one-off events.",
                "first_step": "Create small mixed cohorts around study habits, wellbeing, and course goals.",
            },
            {
                "name": "Fast Friction Desk",
                "why": "It removes small academic blockers before they become stress.",
                "first_step": "Run a weekly pop-up with study advisors and trained student hosts.",
            },
        ]
    if not isinstance(all_ideas, list) or not all_ideas:
        all_ideas = [item.get("name", f"Solution {index}") for index, item in enumerate(best_three, start=1)]
    if not initial_plan:
        initial_plan = "Combine early support, peer belonging, and course engagement signals into one visible student support route."

    state.generated_solution_count = count
    state.best_solutions = best_three[:3]
    state.ideas.extend(one_line(item) for item in all_ideas[:30] if one_line(item))
    state.plan_history.append(initial_plan)
    return count, best_three[:3], initial_plan


def intervention_times(args):
    if args.intervention_seconds:
        values = []
        for part in args.intervention_seconds.split(","):
            part = part.strip()
            if part:
                values.append(float(part))
        if values:
            return values[:3]
    return [0.0, args.total_seconds / 2.0, args.total_seconds]


def run_pregenerated(args, input_thread, send_fn, presenter):
    state = BrainstormState(session_kind="pregenerated")
    configure_transcript_logging(state, args)
    static_content, static_content_path = load_pregenerated_content(args)
    times = intervention_times(args)
    start = time.monotonic()
    next_intervention_index = 0

    def record_setup2_participant_line(line, trigger_reason="setup 2 participant input"):
        if not one_line(line):
            return None
        speaker, text = parse_speaker_line(line)
        text = normalize_transcript_aliases(text)
        state.add_participant(
            text,
            speaker=speaker,
            trigger_reason=trigger_reason,
            triggered_robot=False,
            source="deepgram" if args.deepgram_live else "console",
        )
        state.ideas.append(f"{speaker}: {text}")
        return text

    def wait_for_setup2_post_cue_quiet(key):
        gate = getattr(input_thread, "speech_gate", None)
        if not gate or not args.deepgram_live:
            return None

        grace_seconds = max(0.0, float(getattr(args, "setup2_post_cue_grace_seconds", 0.4) or 0.0))
        quiet_seconds = max(0.0, float(getattr(args, "setup2_post_cue_quiet_seconds", 0.8) or 0.0))
        max_wait = max(
            grace_seconds + quiet_seconds,
            float(getattr(args, "setup2_post_cue_max_wait_seconds", 8.0) or 8.0),
        )
        wait_started = time.monotonic()
        print(f"[Setup 2] Waiting for participants to finish before {key}...")
        while time.monotonic() - wait_started < max_wait:
            kind, line = input_thread.get(timeout=0.15)
            if kind == "__ERROR__":
                print(f"[Input] {line}")
                continue
            if kind == "line":
                cmd = command_of(line)
                if cmd in {"EXIT", "QUIT", "STOP"}:
                    return "stop"
                if wants_to_stop_session(line):
                    record_setup2_participant_line(
                        line,
                        trigger_reason=f"setup 2 participant asked to stop after {key} cue",
                    )
                    return "stop"
                if cmd not in {"ROBOT", "INTERVENE", "NEXT"}:
                    record_setup2_participant_line(
                        line,
                        trigger_reason=f"setup 2 participant continued after {key} cue",
                    )
                continue

            if time.monotonic() - wait_started < grace_seconds:
                continue
            if gate.participant_quiet_for(quiet_seconds):
                print(f"[Setup 2] Room quiet; continuing with {key}.")
                return None

        print(f"[Setup 2] Max wait reached; continuing with {key}.")
        return None

    def maybe_intervene(force=False):
        nonlocal next_intervention_index
        if next_intervention_index >= 3:
            return None
        elapsed = time.monotonic() - start
        if not force and elapsed < times[next_intervention_index]:
            return None

        number = next_intervention_index + 1
        next_intervention_index += 1

        if number == 1:
            state.phase = "pregenerated_intervention_1"
            say_setup2_attention_cue(args, state, send_fn, "intervention_1")
            if wait_for_setup2_post_cue_quiet("intervention 1") == "stop":
                return "stop"
            if static_content:
                count, best, initial_plan = apply_static_solution_bank(static_content, state)
                speech = static_section(static_content, "intervention_1_speech") or (
                    f"I will keep this brainstorm structured and practical. I prepared {count} candidate solutions in advance. "
                    f"The strongest three are: {format_best_three(best)} My starting plan is: {initial_plan}"
                )
                if "intervention_1" in (static_content.get("infographics") or {}):
                    spec = static_infographic(
                        static_content,
                        "intervention_1",
                        "Prepared Solution Set",
                        "Pepper's pre-authored strongest directions.",
                        state.ideas,
                        initial_plan,
                    )
                    presenter.present(
                        spec,
                        "pregenerated_intervention_1.html",
                        image_path=static_image_path(static_content, static_content_path, "intervention_1"),
                    )
                    say_thinking_cue(args, state, send_fn, "intervention_1")
            else:
                count, best, initial_plan = generate_solution_bank(args, state)
                speech = (
                    f"I will keep this brainstorm structured and practical. I generated {count} candidate solutions. The strongest three are: "
                    f"{format_best_three(best)} My starting plan is: {initial_plan}"
                )
            say(state, send_fn, speech, max_chars=1300, trigger_reason="setup 2 intervention 1")
            return "intervened"

        if number == 2:
            state.phase = "pregenerated_intervention_2"
            say_setup2_attention_cue(args, state, send_fn, "intervention_2")
            if wait_for_setup2_post_cue_quiet("intervention 2") == "stop":
                return "stop"
            say_setup2_listening_reflection(args, state, send_fn, "intervention_2")
            plan = ""
            if static_content:
                plan = one_line(static_section(static_content, "intervention_2_plan") or "")
                if "intervention_2" in (static_content.get("infographics") or {}):
                    spec = static_infographic(
                        static_content,
                        "intervention_2",
                        "Integrated Midpoint Plan",
                        "Pepper's prepared synthesis after the first discussion block.",
                        state.ideas,
                        plan,
                    )
                    presenter.present(
                        spec,
                        "pregenerated_intervention_2.html",
                        image_path=static_image_path(static_content, static_content_path, "intervention_2"),
                    )
                    say_thinking_cue(args, state, send_fn, "intervention_2")
            if not plan:
                plan = generate_plan_update(args, state, number)
            state.plan_history.append(plan)
            say(state, send_fn, plan, max_chars=1300, trigger_reason="setup 2 intervention 2")
            return "intervened"

        state.phase = "final_collection"
        say_setup2_attention_cue(args, state, send_fn, "closing")
        if wait_for_setup2_post_cue_quiet("closing") == "stop":
            return "stop"
        say_setup2_listening_reflection(args, state, send_fn, "closing")
        say(
            state,
            send_fn,
            static_section(static_content, "closing_speech")
            or "The brainstorm session is over. Thank you everyone. Please share your final plan now. When the room is quiet, I will synthesize the final proposal.",
            trigger_reason="setup 2 closing intervention",
        )
        return "final"

    final_requested = False
    session_deadline = start + args.total_seconds

    while time.monotonic() < session_deadline and not final_requested:
        maybe = maybe_intervene(force=False)
        if maybe == "stop":
            return state
        if maybe == "final":
            final_requested = True
            break
        kind, line = input_thread.get(timeout=0.3)
        if kind != "line":
            continue
        cmd = command_of(line)
        if cmd in {"EXIT", "QUIT", "STOP"}:
            return state
        if wants_to_stop_session(line):
            record_setup2_participant_line(line, trigger_reason="setup 2 participant asked to stop the session")
            return state
        if cmd in {"ROBOT", "INTERVENE", "NEXT"}:
            maybe = maybe_intervene(force=True)
            if maybe == "stop":
                return state
            if maybe == "final":
                final_requested = True
            continue
        if wants_to_finish_or_has_no_more_input(line):
            record_setup2_participant_line(line, trigger_reason="setup 2 participant said there is nothing more to add")
            maybe = maybe_intervene(force=True)
            if maybe == "stop":
                return state
            if maybe == "final":
                final_requested = True
            continue
        if not one_line(line):
            continue
        record_setup2_participant_line(line)

    while not final_requested and next_intervention_index < 3:
        maybe = maybe_intervene(force=True)
        if maybe == "stop":
            return state
        if maybe == "final":
            final_requested = True

    final_args = argparse.Namespace(**vars(args))
    final_args.final_silence_seconds = max(0.0, float(getattr(args, "setup2_final_silence_seconds", 4.0) or 0.0))
    final_args.final_empty_silence_seconds = max(0.0, float(getattr(args, "setup2_final_empty_silence_seconds", 4.0) or 0.0))
    final_args.final_silence_gate_grace_seconds = max(0.0, float(getattr(args, "setup2_final_gate_grace_seconds", 2.0) or 0.0))
    final_collection_result = collect_final_ideas(final_args, state, input_thread, send_fn, args.final_collection_seconds, announce=False)
    if final_collection_result == "stop":
        return state
    state.phase = "final_synthesis"
    say_setup2_listening_reflection(args, state, send_fn, "final")
    if static_content:
        print("[Synthesis] Using static authored final plan.")
        final_plan = one_line(static_section(static_content, "final_plan") or "")
        if not final_plan:
            final_plan = "My final plan is the Delft Student Support Loop: combine peer support, course engagement signals, and fast wellbeing referrals into one clear route. Start with one first-year pilot, train peer mentors, connect course teams to early signals, and measure belonging, stress, participation, and response time."
    else:
        print("[Synthesis] Generating Pepper's final plan with the local LLM.")
        final_plan = generate_final_cloud_solution(args, state)
    final_plan = add_end_of_session_closing(final_plan)
    state.plan_history.append(final_plan)
    print("[Synthesis] Preparing the tablet infographic.")
    if static_content and "final" in (static_content.get("infographics") or {}):
        image_path = static_image_path(static_content, static_content_path, "final")
        spec = static_infographic(
            static_content,
            "final",
            "Final Solution Plan",
            "A synthesized wellbeing and engagement route for TU Delft.",
            state.ideas,
            final_plan,
        )
    else:
        image_path = generate_cloud_infographic_image(args, state, final_plan, "pregenerated_final.png")
        spec = make_infographic_spec(
            args,
            state,
            title="Final Solution Plan",
            subtitle="A synthesized wellbeing and engagement route for TU Delft.",
            plan_text=final_plan,
            prefer_cloud=True,
        )
    presenter.present(spec, "pregenerated_final.html", image_path=image_path)
    say_thinking_cue(args, state, send_fn, "final")
    say(state, send_fn, final_plan, max_chars=1400, trigger_reason="setup 2 final synthesis")
    return state


def write_session_log(state, args):
    if not args.brainstorm_log:
        return None
    log_path = pathlib.Path(args.brainstorm_log)
    if not log_path.is_absolute():
        log_path = ROOT / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_kind": state.session_kind,
        "session_id": state.session_id,
        "group_id": state.group_id,
        "conversation_id": state.conversation_id,
        "transcript_log_path": str(state.transcript_log_path) if state.transcript_log_path else "",
        "question": state.question,
        "participant_turns": [turn.__dict__ for turn in state.participant_turns],
        "robot_turns": [turn.__dict__ for turn in state.robot_turns],
        "ideas": state.ideas,
        "final_ideas": state.final_ideas,
        "plan_history": state.plan_history,
        "generated_solution_count": state.generated_solution_count,
        "best_solutions": state.best_solutions,
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return log_path


def build_receive_fn(args, speech_gate=None):
    if args.deepgram_live:
        if not args.deepgram_api_key:
            raise RuntimeError("--deepgram-api-key is required with --deepgram-live")
        separate_channels = resolve_audio_separate_channels(args.audio_separate_channels, args.audio_input_channels)
        if separate_channels:
            print("[Audio] Two-speaker transcript mode is on. Channel 1 and channel 2 will be transcribed separately.")
        return build_deepgram_live_receiver(
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
            on_speech_start=speech_gate.mark_participant_speech_start if speech_gate else None,
            on_speech_end=speech_gate.mark_participant_speech_end if speech_gate else None,
        )
    return console_receive


def add_common_args(parser):
    parser.add_argument("--session", choices=["dynamic", "pregenerated"])
    parser.add_argument("--server-url", default="http://127.0.0.1:1234/v1/chat/completions")
    parser.add_argument("--local-model", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--auto-start-lmstudio", action=argparse.BooleanOptionalAction, default=True, help="Auto-run `lms server start` when the default local LM Studio server is off")
    parser.add_argument("--brainstorm-log", default="logs/brainstorm_latest.json")
    parser.add_argument("--transcript-log", default="logs/transcript.csv", help="CSV transcript/event log path; pass an empty string to disable")
    parser.add_argument("--session-id", default="S01", help="Session identifier written to transcript CSV")
    parser.add_argument("--group-id", default="G01", help="Group identifier written to transcript CSV")
    parser.add_argument("--conversation-id", default="", help="Optional conversation identifier written to transcript CSV")
    parser.add_argument("--human-cues", action=argparse.BooleanOptionalAction, default=True, help="Use small human-like spoken cues around visuals and live responses")
    parser.add_argument("--pepper", action="store_true", help="Speak Pepper outputs through Pepper TTS")
    parser.add_argument("--mock-pepper", action="store_true", help="Test the full session without connecting to a physical Pepper robot")
    parser.add_argument("--pepper-optional", action="store_true", help="Continue in mock mode if Pepper TTS/tablet calls fail")
    parser.add_argument("--pepper-ip", default="192.168.1.35")
    parser.add_argument("--pepper-port", type=int, default=9559)
    parser.add_argument("--pepper-auto-discover", action=argparse.BooleanOptionalAction, default=True, help="When Pepper is enabled, find a reachable NAOqi IP automatically if --pepper-ip is auto or unreachable")
    parser.add_argument("--pepper-connect-timeout", type=float, default=1.0, help="Seconds to wait when checking whether the configured Pepper IP is reachable")
    parser.add_argument("--pepper-discovery-timeout", type=float, default=0.16, help="Per-IP timeout while scanning 169.254.x.x for Pepper")
    parser.add_argument("--pepper-discovery-max-seconds", type=float, default=10.0, help="Maximum time spent scanning direct Ethernet/link-local IPs for Pepper")
    parser.add_argument("--pepper-language", default="English")
    parser.add_argument("--pepper-vocabulary")
    parser.add_argument("--tts-speed", type=float, default=None, help="Pepper speech speed as percent; project default is 110")
    parser.add_argument("--tts-volume", type=float, default=None, help="Pepper speech volume from 0.0 to 1.0; project default is 0.8")
    parser.add_argument("--tts-pitch", type=float, default=None, help="Pepper pitch shift; project default is 1.0")
    parser.add_argument("--tts-pause-ms", type=int, default=None, help="Pause inserted after sentence punctuation in Pepper speech")
    parser.add_argument("--tts-voice", default=None, help="Optional exact Pepper voice name")
    parser.add_argument("--look-at-people", action=argparse.BooleanOptionalAction, default=True, help="Have Pepper look toward/track people before speaking")
    parser.add_argument("--pepper-legacy-python", default=default_py27_command())
    parser.add_argument("--pepper-legacy-tts-script", default=str(ROOT.parent / "pepper" / "tts.py"))
    parser.add_argument("--pepper-legacy-tablet-script", default=str(ROOT.parent / "pepper" / "tablet.py"))
    parser.add_argument("--tablet", action="store_true", help="Serve and show infographic pages on Pepper tablet")
    parser.add_argument("--laptop-display", action="store_true", help="Show setup infographics on this laptop screen in a browser window")
    parser.add_argument("--tablet-port", type=int, default=8008)
    parser.add_argument("--tablet-host", help="Computer IP address reachable by Pepper; auto-detected by default")
    parser.add_argument("--deepgram-live", action="store_true")
    parser.add_argument("--deepgram-api-key")
    parser.add_argument("--deepgram-record-seconds", type=float, default=10.0, help="Maximum utterance length in endpointing mode; fixed chunk length when --no-audio-endpointing is used")
    parser.add_argument("--deepgram-endpoint", default="https://api.eu.deepgram.com/v1/listen")
    parser.add_argument("--deepgram-press-enter", action="store_true", help="Require Enter before each Deepgram recording chunk; continuous listening is the default")
    parser.add_argument("--deepgram-test-once", action="store_true", help="Record one microphone chunk, print the Deepgram transcript, and exit")
    parser.add_argument("--audio-input-device", help="Optional sounddevice input device index or name, e.g. 2 for Focusrite Analogue 1+2")
    parser.add_argument("--audio-prefer-device-name", help="Prefer the first input device whose name contains this text, e.g. Focusrite")
    parser.add_argument("--audio-fallback-to-default-input", action=argparse.BooleanOptionalAction, default=False, help="If the preferred/selected input is unavailable, use the default laptop/default microphone instead")
    parser.add_argument("--audio-fallback-channels", type=int, default=1, help="Channel count to use when falling back to the default input")
    parser.add_argument("--audio-fallback-sample-rate", type=int, default=0, help="Sample rate to use when falling back; 0 uses the default input's native rate")
    parser.add_argument("--audio-input-channels", type=int, default=1, help="Input channels to record; use 2 for two Focusrite microphone inputs")
    parser.add_argument("--audio-sample-rate", type=int, default=0, help="Input sample rate; 0 uses the selected device default, useful for Focusrite devices")
    parser.add_argument("--audio-separate-channels", action=argparse.BooleanOptionalAction, default=None, help="Split audio channels 1 and 2, transcribe separately, and label speakers; auto-enabled when --audio-input-channels is 2 or more")
    parser.add_argument("--audio-channel-names", default="Participant 1,Participant 2", help="Comma-separated names for separate channel mode")
    parser.add_argument("--audio-channel-min-peak", type=int, default=250, help="Skip a separated channel if its peak level is below this raw PCM value")
    parser.add_argument("--audio-channel-relative-peak", type=float, default=0.25, help="Skip a separated channel whose peak is far below the loudest channel, which reduces Focusrite channel bleed")
    parser.add_argument("--audio-channel-relative-rms", type=float, default=0.20, help="Skip a separated channel whose RMS is far below the loudest channel, which reduces duplicate transcripts")
    parser.add_argument("--audio-endpointing", action=argparse.BooleanOptionalAction, default=True, help="Record until speech ends instead of fixed chunks; enabled by default for Deepgram live")
    parser.add_argument("--audio-endpoint-silence-seconds", type=float, default=None, help="How long silence must last before an utterance is sent to Deepgram")
    parser.add_argument("--audio-endpoint-idle-seconds", type=float, default=0.6, help="How long to wait for speech before returning an empty transcript")
    parser.add_argument("--audio-speech-peak-threshold", type=int, default=None, help="Raw PCM peak threshold used to detect speech start/end")
    parser.add_argument("--pre-speech-guard", action=argparse.BooleanOptionalAction, default=True, help="Before Pepper speaks, briefly sample the microphone and wait if participants are still talking")
    parser.add_argument("--pre-speech-quiet-seconds", type=float, default=None, help="How much live quiet is required right before Pepper starts speaking")
    parser.add_argument("--pre-speech-max-wait-seconds", type=float, default=None, help="Maximum time Pepper waits for live quiet before speaking")
    parser.add_argument("--pre-speech-max-wait-action", choices=["speak", "defer"], default=None, help="What to do if the pre-speech guard never finds quiet; dynamic defaults to defer, pregenerated defaults to speak")
    parser.add_argument("--pre-speech-probe-seconds", type=float, default=0.12, help="Length of each quick live audio check before Pepper speaks")
    parser.add_argument("--pre-speech-peak-threshold", type=int, default=None, help="Peak threshold used by the live pre-speech guard")
    parser.add_argument("--final-collection-seconds", type=float, default=300.0)
    parser.add_argument("--final-silence-seconds", type=float, default=8.0, help="After final input is captured, synthesize automatically after this many quiet seconds; use 0 to require DONE or timeout")
    parser.add_argument("--final-empty-silence-seconds", type=float, default=8.0, help="If nobody gives a final idea, synthesize after this many quiet seconds")
    parser.add_argument("--final-silence-gate-grace-seconds", type=float, default=3.0, help="If the audio gate stays busy after the final silence window, synthesize after this many extra seconds")
    parser.add_argument("--final-min-ideas", type=int, default=2, help="Expected number of final ideas for console reminders; silence can still auto-trigger synthesis")


def build_parser():
    parser = argparse.ArgumentParser(description="Pepper brainstorm setups for TU Delft wellbeing")
    add_common_args(parser)
    parser.add_argument("--divergence-seconds", type=float, default=600.0)
    parser.add_argument("--convergence-seconds", type=float, default=600.0)
    parser.add_argument("--dynamic-silence-seconds", type=float, default=4.0, help="Dynamic setup only: Pepper replies after this many seconds of participant silence, or immediately when addressed by name")
    parser.add_argument("--dynamic-response-hold-seconds", type=float, default=0.55, help="Dynamic setup only: wait this long after the latest transcript chunk before responding, to avoid mid-sentence interruptions")
    parser.add_argument("--dynamic-direct-response-hold-seconds", type=float, default=0.25, help="Dynamic setup only: wait this long after direct Pepper/robot address before responding")
    parser.add_argument("--dynamic-struggle-response-hold-seconds", type=float, default=0.4, help="Dynamic setup only: wait this long after hesitation/stuck language before responding")
    parser.add_argument("--dynamic-response-quiet-seconds", type=float, default=0.55, help="Dynamic setup only: require this much detected quiet before normal replies")
    parser.add_argument("--dynamic-direct-response-quiet-seconds", type=float, default=0.3, help="Dynamic setup only: require this much detected quiet before direct Pepper/robot replies")
    parser.add_argument("--dynamic-struggle-response-quiet-seconds", type=float, default=0.4, help="Dynamic setup only: require this much detected quiet before stuck/hesitation replies")
    parser.add_argument("--dynamic-auto-response-quiet-seconds", type=float, default=0.6, help="Dynamic setup only: require this much detected quiet before scheduled proactive interventions")
    parser.add_argument("--dynamic-active-quiet-seconds", type=float, default=1.2, help="Dynamic setup only: after active participant discussion, Pepper may join after this shorter quiet window")
    parser.add_argument("--dynamic-active-quiet-turns", type=int, default=1, help="Dynamic setup only: number of pending transcript chunks that count as active discussion")
    parser.add_argument("--dynamic-active-quiet-words", type=int, default=8, help="Dynamic setup only: number of pending participant words that count as active discussion")
    parser.add_argument("--dynamic-speech-gate-max-block-seconds", type=float, default=1.2, help="Dynamic setup only: if the speech gate stays active this long, treat it as noise and allow Pepper to join")
    parser.add_argument("--dynamic-robot-idle-seconds", type=float, default=0.0, help="Dynamic setup only: optional self-chain after Pepper speaks; 0 disables it")
    parser.add_argument("--dynamic-intervention-cooldown-seconds", type=float, default=8.0, help="Dynamic setup only: minimum pause between Pepper interventions, except direct Pepper/robot requests and struggle language")
    parser.add_argument("--dynamic-low-novelty-recent-turns", type=int, default=4, help="Dynamic setup only: participant turns to inspect for repeated discussion")
    parser.add_argument("--dynamic-low-novelty-comparison-turns", type=int, default=8, help="Dynamic setup only: earlier participant turns to compare against")
    parser.add_argument("--dynamic-low-novelty-new-words", type=int, default=5, help="Dynamic setup only: trigger if recent turns add fewer than this many new content words")
    parser.add_argument("--auto-idea-interval-seconds", type=float, default=35.0)
    parser.add_argument("--convergence-prompt-interval-seconds", type=float, default=60.0)
    parser.add_argument("--total-seconds", type=float, default=1200.0)
    parser.add_argument("--intervention-seconds", help="Comma-separated intervention seconds for pregenerated setup, e.g. 120,600,1200")
    parser.add_argument("--setup2-post-cue-grace-seconds", type=float, default=0.4, help="Pregenerated setup only: minimum time to listen after an attention cue before continuing")
    parser.add_argument("--setup2-post-cue-quiet-seconds", type=float, default=0.8, help="Pregenerated setup only: continue after participants have been quiet this long after the attention cue")
    parser.add_argument("--setup2-post-cue-max-wait-seconds", type=float, default=8.0, help="Pregenerated setup only: longest Pepper waits after an attention cue before continuing")
    parser.add_argument("--setup2-listen-pause-seconds", type=float, default=0.4, help="Pregenerated setup only: silent listening pause before later prepared synthesis moments")
    parser.add_argument("--setup2-final-silence-seconds", type=float, default=4.0, help="Pregenerated setup only: synthesize the final plan after this many quiet seconds after final input")
    parser.add_argument("--setup2-final-empty-silence-seconds", type=float, default=4.0, help="Pregenerated setup only: synthesize the final plan after this many quiet seconds if nobody gives a final idea")
    parser.add_argument("--setup2-final-gate-grace-seconds", type=float, default=2.0, help="Pregenerated setup only: extra seconds before final synthesis if the audio gate stays busy")
    parser.add_argument("--pregenerated-static", action="store_true", help="Pregenerated setup only: use authored ideas, speeches, final plan, and infographic specs from --pregenerated-content-file")
    parser.add_argument("--pregenerated-content-file", default=str(DEFAULT_PREGENERATED_CONTENT), help="JSON file containing static authored setup 2 content")
    parser.add_argument("--pregenerated-text-provider", choices=["local", "openai", "auto"], default="local", help="Pregenerated setup only: local uses LM Studio/Qwen text; openai tries OpenAI text then falls back; auto tries OpenAI only when a key is set")
    parser.add_argument("--openai-api-key", default=None)
    parser.add_argument("--openai-text-model", default=DEFAULT_OPENAI_TEXT_MODEL)
    parser.add_argument("--openai-image-model", default=DEFAULT_OPENAI_IMAGE_MODEL)
    parser.add_argument("--openai-image-size", default="1536x1024")
    parser.add_argument("--openai-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--generate-cloud-image", action="store_true")
    return parser


def load_env_defaults(args):
    import os

    args.openai_api_key = args.openai_api_key or os.environ.get("OPENAI_API_KEY")
    args.openai_text_model = os.environ.get("OPENAI_TEXT_MODEL", args.openai_text_model)
    args.openai_image_model = os.environ.get("OPENAI_IMAGE_MODEL", args.openai_image_model)
    if not args.local_model:
        args.local_model = DEFAULT_PREGENERATED_LOCAL_MODEL if args.session == "pregenerated" else DEFAULT_LOCAL_MODEL
    return args


def apply_session_timing_defaults(args):
    is_dynamic = getattr(args, "session", "") == "dynamic"
    if getattr(args, "pre_speech_quiet_seconds", None) is None:
        args.pre_speech_quiet_seconds = 0.45 if is_dynamic else 0.55
    if getattr(args, "pre_speech_max_wait_seconds", None) is None:
        args.pre_speech_max_wait_seconds = 1.5 if is_dynamic else 2.5
    if getattr(args, "pre_speech_max_wait_action", None) is None:
        args.pre_speech_max_wait_action = "speak"
    if getattr(args, "audio_endpoint_silence_seconds", None) is None:
        args.audio_endpoint_silence_seconds = 1.3 if is_dynamic else 1.2
    if getattr(args, "pre_speech_peak_threshold", None) is None:
        args.pre_speech_peak_threshold = 1200 if is_dynamic else 500
    if getattr(args, "audio_speech_peak_threshold", None) is None:
        args.audio_speech_peak_threshold = 350 if is_dynamic else 120
    return args


def main():
    parser = build_parser()
    args = load_env_defaults(parser.parse_args())
    args = apply_session_timing_defaults(args)
    args = configure_audio_input_device(args)
    args = configure_pepper_ip(args)
    if args.deepgram_test_once:
        if not args.deepgram_api_key:
            parser.exit(2, "--deepgram-api-key is required with --deepgram-test-once\n")
        receive_fn = build_deepgram_live_receiver(
            api_key=args.deepgram_api_key,
            endpoint=args.deepgram_endpoint,
            record_seconds=args.deepgram_record_seconds,
            input_device=args.audio_input_device,
            channels=args.audio_input_channels,
            sample_rate=args.audio_sample_rate,
            wait_for_enter=args.deepgram_press_enter,
            separate_channels=resolve_audio_separate_channels(args.audio_separate_channels, args.audio_input_channels),
            channel_names=args.audio_channel_names,
            channel_min_peak=args.audio_channel_min_peak,
            channel_relative_peak=args.audio_channel_relative_peak,
            channel_relative_rms=args.audio_channel_relative_rms,
            endpointing=args.audio_endpointing,
            endpoint_silence_seconds=args.audio_endpoint_silence_seconds,
            endpoint_idle_seconds=args.audio_endpoint_idle_seconds,
            speech_peak_threshold=args.audio_speech_peak_threshold,
        )
        transcript = receive_fn()
        if isinstance(transcript, (list, tuple)):
            print("[Deepgram test] Final transcripts:")
            for item in transcript:
                print(f"  {item}")
        else:
            print(f"[Deepgram test] Final transcript: {transcript or '<empty>'}")
        return

    if not args.session:
        parser.error("--session is required unless --deepgram-test-once is used")

    needs_local_llm = not (args.session == "pregenerated" and args.pregenerated_static)
    if needs_local_llm:
        try:
            check_lmstudio_ready(args)
        except Exception as error:
            parser.exit(2, f"[LM Studio] {error}\n")
    else:
        print("[LM Studio] Skipped: setup 2 is using static authored content.")

    presenter = TabletPresenter(args)
    speech_gate = SpeechGate()
    send_fn = make_send_fn(args, speech_gate=speech_gate)
    receive_fn = build_receive_fn(args, speech_gate=speech_gate)
    input_thread = ConsoleInputThread(receive_fn, speech_gate=speech_gate if args.deepgram_live else None)

    presenter.start()
    input_thread.start()
    try:
        if args.session == "dynamic":
            state = run_dynamic(args, input_thread, send_fn, presenter)
        else:
            state = run_pregenerated(args, input_thread, send_fn, presenter)
        log_path = write_session_log(state, args)
        if log_path:
            print(f"[Log] Session saved to {log_path}")
    except KeyboardInterrupt:
        print("\n[Stopped]")
    finally:
        input_thread.stop()
        presenter.stop()


if __name__ == "__main__":
    main()
