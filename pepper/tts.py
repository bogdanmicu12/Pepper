#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import sys
import os


def add_naoqi_paths():
	candidates = []
	env_path = os.environ.get("NAOQI_PYTHONPATH")
	if env_path:
		candidates.extend([item for item in env_path.split(os.pathsep) if item])

	candidates.extend([
		r"C:\naoqi-sdk\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\lib",
		r"C:\naoqi-sdk\pynaoqi-python2.7-2.5.7.1-win32-vs2013\lib",
		r"C:\Users\bogda\Downloads\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\lib",
		r"C:\Python27\Lib\site-packages",
	])

	for root in [r"C:\naoqi-sdk", r"C:\Users\bogda\Downloads", r"C:\Program Files", r"C:\Program Files (x86)"]:
		if not os.path.isdir(root):
			continue
		for current, dirs, files in os.walk(root):
			if "naoqi.py" in files or "naoqi.pyd" in files:
				candidates.append(current)
			if current.count(os.sep) - root.count(os.sep) >= 4:
				dirs[:] = []

	for candidate in reversed(candidates):
		if candidate and os.path.isdir(candidate) and candidate not in sys.path:
			sys.path.insert(0, candidate)


add_naoqi_paths()

import argparse
try:
	from naoqi import ALProxy
except ImportError:
	raise ImportError(
		"Could not import naoqi. Install the Python 2.7 pynaoqi SDK, or set "
		"NAOQI_PYTHONPATH to the SDK lib directory before running this helper."
	)

DEFAULT_VOICE_CANDIDATES = ("naoenu", "naoeng")


def set_preferred_voice(tts, voice=None):
	candidates = []
	if voice:
		candidates.append(str(voice))
	candidates.extend(DEFAULT_VOICE_CANDIDATES)

	try:
		available = tts.getAvailableVoices()
	except Exception:
		available = []

	available_by_lower = {}
	for item in available:
		available_by_lower[str(item).lower()] = item

	for candidate in candidates:
		selected = available_by_lower.get(str(candidate).lower())
		if selected:
			try:
				tts.setVoice(selected)
			except Exception:
				pass
			return


def set_vocal_params(tts, speed=100, volume=0.7, pitch=1.0, voice=None):
	set_preferred_voice(tts, voice=voice)
	try:
		speed_value = float(speed)
		if speed_value <= 2.0:
			speed_value *= 100.0
		tts.setParameter("speed", speed_value)
	except Exception:
		pass

	try:
		tts.setVolume(float(volume))
	except Exception:
		pass

	try:
		tts.setParameter("pitch", float(pitch))
	except Exception:
		try:
			if float(pitch) >= 1.0:
				tts.setParameter("pitchShift", float(pitch))
		except Exception:
			pass


def main():
	parser = argparse.ArgumentParser(description="Pepper TTS helper (Python 2.7)")
	parser.add_argument("--ip", default="192.168.1.35", help="Pepper robot IP")
	parser.add_argument("--port", type=int, default=9559, help="Pepper NAOqi port")
	parser.add_argument("--say", default="Hello, world!", help="Text to speak")
	parser.add_argument("--speed", type=float, default=102.0, help="Speech speed percentage; 100 is Pepper default")
	parser.add_argument("--volume", type=float, default=0.74, help="Speech volume from 0.0 to 1.0")
	parser.add_argument("--pitch", type=float, default=1.02, help="Best-effort pitch level")
	parser.add_argument("--voice", default="naoenu", help="Preferred installed Pepper voice name")
	args = parser.parse_args()

	tts = ALProxy("ALTextToSpeech", args.ip, args.port)
	set_vocal_params(tts, speed=args.speed, volume=args.volume, pitch=args.pitch, voice=args.voice)
	tts.say(args.say)


if __name__ == "__main__":
	main()
