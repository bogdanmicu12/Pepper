#!/usr/bin/env python
from __future__ import print_function

import argparse
import os
import sys


def configure_naoqi_sdk():
	sdk_root = os.environ.get("NAOQI_SDK_ROOT")
	if not sdk_root:
		return

	sdk_lib = os.path.join(sdk_root, "lib")
	sdk_bin = os.path.join(sdk_root, "bin")

	if os.path.exists(os.path.join(sdk_lib, "naoqi.py")) and sdk_lib not in sys.path:
		sys.path.insert(0, sdk_lib)

	path_parts = os.environ.get("PATH", "").split(os.pathsep)
	for path in (sdk_bin, sdk_lib):
		if os.path.isdir(path) and path not in path_parts:
			path_parts.insert(0, path)
	os.environ["PATH"] = os.pathsep.join(path_parts)


configure_naoqi_sdk()
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
