"""Recoverable small JSON writes, with a verified previous generation.

LittleFS rename is atomic on the target. A power loss between renames leaves
the .prev copy readable. Never reset damaged configuration silently.
"""
import os
from compat import json

def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False

def read_json(path, default=None):
    for candidate in (path, path + ".prev"):
        try:
            with open(candidate, "r") as stream:
                return json.load(stream)
        except (OSError, ValueError):
            pass
    return default

def sync():
    if hasattr(os, "sync"):
        os.sync()

def atomic_json(path, data):
    temp = path + ".tmp"
    with open(temp, "w") as stream:
        json.dump(data, stream)
        stream.flush()
    sync()
    with open(temp, "r") as stream:
        if json.load(stream) != data:
            raise OSError("Configuration readback failed")
    if exists(path):
        try:
            with open(path, "r") as stream:
                json.load(stream)
            current_valid = True
        except (OSError, ValueError):
            current_valid = False
        if current_valid:
            if exists(path + ".prev"):
                os.remove(path + ".prev")
            os.rename(path, path + ".prev")
        else:
            # A corrupt current generation must never replace the only good
            # backup during repair, even if power fails before the final rename.
            os.remove(path)
    os.rename(temp, path)
    sync()
