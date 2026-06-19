#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def load_local_env_file(env_path=None):
    path = env_path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return

    with open(path, "r") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _candidate_paths():
    candidates = []

    env_pythonpath = os.environ.get("NAOQI_PYTHONPATH")
    if env_pythonpath:
        candidates.extend([item for item in env_pythonpath.split(os.pathsep) if item])

    sdk_root = os.environ.get("NAOQI_SDK_ROOT")
    if sdk_root:
        candidates.extend([
            os.path.join(sdk_root, "lib"),
            os.path.join(sdk_root, "bin"),
        ])

    candidates.extend([
        r"C:\Python27\Lib\site-packages",
    ])
    return candidates


def _search_roots():
    roots = [r"C:\naoqi-sdk", r"C:\Program Files", r"C:\Program Files (x86)"]
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        roots.insert(1, os.path.join(user_profile, "Downloads"))
    return roots


def add_naoqi_paths(max_depth=4):
    """Add likely NAOqi SDK locations to sys.path and PATH for Python 2.7 helpers."""
    load_local_env_file()
    candidates = list(_candidate_paths())

    for root in _search_roots():
        if not os.path.isdir(root):
            continue
        for current, dirs, files in os.walk(root):
            if "naoqi.py" in files or "naoqi.pyd" in files:
                candidates.append(current)
            if current.count(os.sep) - root.count(os.sep) >= int(max_depth):
                dirs[:] = []

    path_parts = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    for candidate in reversed(candidates):
        if not candidate or not os.path.isdir(candidate):
            continue
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
        if candidate not in path_parts:
            path_parts.insert(0, candidate)

    os.environ["PATH"] = os.pathsep.join(path_parts)
