#!/usr/bin/env python
from __future__ import print_function

import argparse
from naoqi import ALProxy


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