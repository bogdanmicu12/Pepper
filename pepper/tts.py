#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import sys
import os

NAOQI_PATHS = [
	os.environ.get("NAOQI_PYTHON_PATH"),
	r"C:\naoqi-sdk\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\lib",
	r"C:\Program Files (x86)\Softbank Robotics\Choregraphe Suite 2.5\lib",
]
NAOQI_DLL_PATHS = [
	os.environ.get("NAOQI_DLL_PATH"),
	r"C:\naoqi-sdk\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\bin",
	r"C:\Program Files (x86)\Softbank Robotics\Choregraphe Suite 2.5\bin",
]

for path in NAOQI_DLL_PATHS:
	if path and os.path.isdir(path):
		os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")

for path in NAOQI_PATHS:
	if path and path not in sys.path:
		sys.path.append(path)

import argparse
from naoqi import ALProxy


def normalize_speed(value):
	try:
		speed = float(value)
	except Exception:
		return 86.0
	if speed <= 3.0:
		speed *= 100.0
	return max(50.0, min(140.0, speed))


def soften_text(text, pause_ms):
	if not text or pause_ms <= 0:
		return text
	pause = "\\pau=%d\\" % int(pause_ms)
	cleaned = " ".join(text.split())
	cleaned = cleaned.replace(". ", ". %s " % pause)
	cleaned = cleaned.replace("? ", "? %s " % pause)
	cleaned = cleaned.replace("! ", "! %s " % pause)
	cleaned = cleaned.replace(": ", ": %s " % pause)
	return cleaned


def apply_tts_settings(tts, args):
	if args.language:
		try:
			tts.setLanguage(args.language)
		except Exception as error:
			print("[Pepper TTS] Could not set language %r: %s" % (args.language, error))
	if args.voice:
		try:
			tts.setVoice(args.voice)
		except Exception as error:
			print("[Pepper TTS] Could not set voice %r: %s" % (args.voice, error))
	for name in ("doubleVoice", "doubleVoiceLevel", "doubleVoiceTimeShift"):
		try:
			tts.setParameter(name, 0.0)
		except Exception:
			pass
	try:
		tts.setVolume(max(0.0, min(1.0, float(args.volume))))
	except Exception:
		pass
	try:
		tts.setParameter("speed", normalize_speed(args.speed))
	except Exception:
		pass
	try:
		tts.setParameter("pitchShift", max(0.7, min(1.3, float(args.pitch))))
	except Exception:
		try:
			tts.setParameter("pitch", max(0.7, min(1.3, float(args.pitch))))
		except Exception:
			pass


def look_at_people(args):
	if not args.look_at_people:
		return

	try:
		motion = ALProxy("ALMotion", args.ip, args.port)
		try:
			motion.wakeUp()
		except Exception:
			pass
		try:
			motion.setStiffnesses("Head", 1.0)
		except Exception:
			pass
		try:
			motion.angleInterpolationWithSpeed(["HeadYaw", "HeadPitch"], [0.0, -0.05], 0.35)
		except Exception:
			pass
		return
	except Exception:
		pass

	try:
		awareness = ALProxy("ALBasicAwareness", args.ip, args.port)
		for call in [
			lambda: awareness.setStimulusDetectionEnabled("People", True),
			lambda: awareness.setTrackingMode("Head"),
			lambda: awareness.setEngagementMode("SemiEngaged"),
			lambda: awareness.startAwareness(),
		]:
			try:
				call()
			except Exception:
				pass
	except Exception:
		pass


def main():
	parser = argparse.ArgumentParser(description="Pepper TTS helper (Python 2.7)")
	parser.add_argument("--ip", default="192.168.1.35", help="Pepper robot IP")
	parser.add_argument("--port", type=int, default=9559, help="Pepper NAOqi port")
	parser.add_argument("--say", default="Hello, world!", help="Text to speak")
	parser.add_argument("--speed", type=float, default=110.0, help="Speech speed as percent; 110 is the project default")
	parser.add_argument("--volume", type=float, default=0.8, help="Speech volume from 0.0 to 1.0")
	parser.add_argument("--pitch", type=float, default=1.0, help="Pitch shift; 1.0 is the project default")
	parser.add_argument("--pause-ms", type=int, default=220, help="Pause inserted after sentence punctuation")
	parser.add_argument("--language", default="English", help="Pepper TTS language")
	parser.add_argument("--voice", default="", help="Optional exact Pepper voice name")
	parser.add_argument("--look-at-people", dest="look_at_people", action="store_true", default=True, help="Track/look toward people before speaking")
	parser.add_argument("--no-look-at-people", dest="look_at_people", action="store_false", help="Do not move Pepper's gaze before speaking")
	parser.add_argument("--list-voices", action="store_true", help="Print installed Pepper voice names and exit")
	args = parser.parse_args()

	tts = ALProxy("ALTextToSpeech", args.ip, args.port)
	if args.list_voices:
		try:
			for voice in tts.getAvailableVoices():
				print(voice)
		except Exception as error:
			print("[Pepper TTS] Could not list voices: %s" % error)
		return
	apply_tts_settings(tts, args)
	look_at_people(args)
	tts.say(soften_text(args.say, args.pause_ms))


if __name__ == "__main__":
	main()
