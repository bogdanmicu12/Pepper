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

	user_profile = os.environ.get("USERPROFILE")
	if user_profile:
		candidates.append(os.path.join(
			user_profile,
			"Downloads",
			"pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649",
			"pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649",
			"lib",
		))

	candidates.extend([
		r"C:\naoqi-sdk\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\lib",
		r"C:\naoqi-sdk\pynaoqi-python2.7-2.5.7.1-win32-vs2013\lib",
		r"C:\Users\Hrsem\Downloads\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\lib",
		r"C:\Users\jaehy\Downloads\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\pynaoqi-python2.7-2.8.6.23-win64-vs2015-20191127_152649\lib",
		r"C:\Python27\Lib\site-packages",
	])

	search_roots = [r"C:\naoqi-sdk", r"C:\Program Files", r"C:\Program Files (x86)"]
	if user_profile:
		search_roots.insert(1, os.path.join(user_profile, "Downloads"))
	search_roots.append(r"C:\Users\jaehy\Downloads")

	for root in search_roots:
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


def set_vocal_params(tts, speed=100, volume=0.7, pitch=1.0):
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
	parser.add_argument("--speed", type=float, default=82.0, help="Speech speed percentage; 100 is Pepper default")
	parser.add_argument("--volume", type=float, default=0.58, help="Speech volume from 0.0 to 1.0")
	parser.add_argument("--pitch", type=float, default=0.97, help="Best-effort pitch level")
	args = parser.parse_args()

	tts = ALProxy("ALTextToSpeech", args.ip, args.port)
	set_vocal_params(tts, speed=args.speed, volume=args.volume, pitch=args.pitch)
	tts.say(args.say)


if __name__ == "__main__":
	main()
