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


def main():
	parser = argparse.ArgumentParser(description="Pepper TTS helper (Python 2.7)")
	parser.add_argument("--ip", default="192.168.1.35", help="Pepper robot IP")
	parser.add_argument("--port", type=int, default=9559, help="Pepper NAOqi port")
	parser.add_argument("--say", default="Hello, world!", help="Text to speak")
	args = parser.parse_args()

	tts = ALProxy("ALTextToSpeech", args.ip, args.port)
	tts.say(args.say)


if __name__ == "__main__":
	main()
