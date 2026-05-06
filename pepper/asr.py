#!/usr/bin/env python
from __future__ import print_function

import argparse
import os
import sys
import time


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


ASR_MEMORY_KEY = "WordRecognized"


def parse_vocabulary(value):
	if not value:
		return []
	return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


def listen(ip, port, language, vocabulary, timeout_seconds, min_confidence):
	memory = ALProxy("ALMemory", ip, port)
	asr = ALProxy("ALSpeechRecognition", ip, port)
	subscriber_name = "pepper_asr_helper_%d" % int(time.time() * 1000)

	try:
		asr.setLanguage(language)
		if vocabulary:
			asr.pause(True)
			asr.setVocabulary(vocabulary, False)
			asr.pause(False)

		asr.subscribe(subscriber_name)
		deadline = time.time() + float(timeout_seconds)
		last_word = ""
		last_word_time = 0.0

		while time.time() < deadline:
			try:
				data = memory.getData(ASR_MEMORY_KEY)
			except Exception:
				data = None

			if isinstance(data, list) and len(data) >= 2:
				for idx in range(0, len(data) - 1, 2):
					word = data[idx]
					confidence = data[idx + 1]
					if not isinstance(word, basestring):
						continue
					try:
						confidence_value = float(confidence)
					except Exception:
						continue

					cleaned = word.strip()
					now = time.time()
					if not cleaned or confidence_value < float(min_confidence):
						continue
					if cleaned == last_word and (now - last_word_time) < 1.0:
						continue

					return cleaned

			time.sleep(0.1)

		return ""
	finally:
		try:
			asr.unsubscribe(subscriber_name)
		except Exception:
			pass


def main():
	parser = argparse.ArgumentParser(description="Pepper ASR helper (Python 2.7)")
	parser.add_argument("--ip", default="192.168.1.35", help="Pepper robot IP")
	parser.add_argument("--port", type=int, default=9559, help="Pepper NAOqi port")
	parser.add_argument("--language", default="English", help="Pepper ASR language")
	parser.add_argument("--vocabulary", help="Comma-separated recognition vocabulary")
	parser.add_argument("--timeout", type=float, default=12.0, help="Seconds to wait for speech")
	parser.add_argument("--min-confidence", type=float, default=0.45, help="Minimum recognition confidence")
	args = parser.parse_args()

	text = listen(
		ip=args.ip,
		port=args.port,
		language=args.language,
		vocabulary=parse_vocabulary(args.vocabulary),
		timeout_seconds=args.timeout,
		min_confidence=args.min_confidence,
	)

	if text:
		sys.stdout.write(text.encode("utf-8"))


if __name__ == "__main__":
	main()
