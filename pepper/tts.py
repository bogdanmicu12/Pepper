#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

from naoqi_paths import add_naoqi_paths


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
