#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import argparse
import os
import sys
import time

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

from naoqi import ALProxy


def main():
    parser = argparse.ArgumentParser(description="Pepper tablet helper (Python 2.7)")
    parser.add_argument("--ip", default="192.168.1.35", help="Pepper robot IP")
    parser.add_argument("--port", type=int, default=9559, help="Pepper NAOqi port")
    parser.add_argument("--url", required=True, help="URL to show on Pepper tablet")
    args = parser.parse_args()

    tablet = ALProxy("ALTabletService", args.ip, args.port)
    try:
        tablet.wakeUp()
    except Exception:
        pass
    try:
        tablet.hideWebview()
        time.sleep(0.2)
    except Exception:
        pass

    errors = []
    try:
        tablet.loadUrl(args.url)
        time.sleep(0.4)
    except Exception as error:
        errors.append("loadUrl: %s" % error)

    try:
        tablet.showWebview()
        return
    except Exception as error:
        errors.append("showWebview(): %s" % error)

    try:
        tablet.showWebview(args.url)
        return
    except Exception as error:
        errors.append("showWebview(url): %s" % error)

    raise RuntimeError("Could not show tablet URL. " + " | ".join(errors))


if __name__ == "__main__":
    main()
