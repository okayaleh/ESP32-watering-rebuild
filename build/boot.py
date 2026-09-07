"""Immutable recovery entrypoint. Flash manually; exclude from OTA manifests.

This file cannot depend on any module that an update may replace. Recovery
is idempotent: original backups survive until a new update is prepared.
"""
import os
try:
    import ujson as json
except ImportError:
    import json


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _sync():
    if hasattr(os, "sync"):
        os.sync()


def _read(path):
    for candidate in (path, path + ".prev"):
        try:
            with open(candidate) as stream:
                return json.load(stream)
        except (OSError, ValueError):
            pass
    return None


def _save(path, data):
    with open(path + ".tmp", "w") as stream:
        json.dump(data, stream)
        stream.flush()
    _sync()
    if _exists(path):
        try:
            with open(path) as stream:
                json.load(stream)
            valid = True
        except (OSError, ValueError):
            valid = False
        if valid:
            if _exists(path + ".prev"):
                os.remove(path + ".prev")
            os.rename(path, path + ".prev")
        else:
            os.remove(path)
    os.rename(path + ".tmp", path)
    _sync()


def close_boot_valves(root=".", pin_class=None):
    if pin_class is None:
        from machine import Pin
        pin_class = Pin
    valves = None
    # JSON parsing alone is insufficient: a torn/manual replacement may be
    # valid JSON but have no usable hardware structure. Check each generation
    # before falling back to first-boot config, preserving custom valve pins.
    for path in (root + "/settings.json", root + "/settings.json.prev"):
        try:
            with open(path) as stream:
                settings = json.load(stream)
            hardware = settings.get("hardware") if isinstance(settings, dict) else None
            candidate = hardware.get("valves") if isinstance(hardware, dict) else None
            if isinstance(candidate, list) and candidate:
                valves = candidate
                break
        except (OSError, ValueError):
            pass
    if valves is None:
        try:
            import config
            valves = config.VALVES
        except (ImportError, AttributeError):
            valves = [{"pin": 26, "active_high": True}]
    if not isinstance(valves, (list, tuple)):
        print("BOOT valve configuration is not a list")
        return False
    success = True
    for item in valves:
        try:
            if (not isinstance(item, dict) or type(item.get("pin")) is not int or
                    not 0 <= item["pin"] <= 48 or
                    type(item.get("active_high", True)) is not bool):
                raise ValueError("Invalid valve pin or polarity; no GPIO guessed")
            pin_class(item["pin"], pin_class.OUT,
                      value=0 if item.get("active_high", True) else 1)
        except Exception as exc:
            success = False
            print("BOOT valve close failed:", exc)
    return success


def boot_guard(root=".", limit=3):
    path = root + "/.ota-journal.json"
    journal = _read(path)
    if not isinstance(journal, dict):
        return "none"
    phase = journal.get("phase")
    if phase in ("stable", "rolled_back"):
        return phase
    if phase == "pending":
        journal["boots"] = journal.get("boots", 0) + 1
        _save(path, journal)
        if journal["boots"] < limit:
            return "trial"
    # An interrupted commit is never allowed to start mixed firmware.
    if phase not in ("pending", "committing", "rolling_back"):
        raise OSError("Unknown OTA recovery state")
    journal["phase"] = "rolling_back"
    _save(path, journal)
    protected = ("boot.py", "boot.mpy", "romboot.py", "romboot.mpy", "config.py", "config.mpy", "wifi.json",
                 "_rom_state.json",
                 "settings.json", "watering_state.json", "history.json", "state.json")
    for entry in journal["entries"]:
        name = entry["name"]
        if (not isinstance(name, str) or name.startswith(".") or
                "/" in name or "\\" in name or ".." in name or name.lower() in protected):
            raise OSError("Unsafe OTA rollback entry")
        destination = root + "/" + name
        if entry["old"]:
            # Copy, never consume backup: power can fail again during rollback.
            with open(root + "/.ota/old-" + name, "rb") as source:
                with open(destination + ".restore", "wb") as target:
                    while True:
                        data = source.read(512)
                        if not data:
                            break
                        target.write(data)
                    target.flush()
            _sync()
            if _exists(destination):
                os.remove(destination)
            os.rename(destination + ".restore", destination)
        elif _exists(destination):
            os.remove(destination)  # Also undo newly added modules.
        _sync()
    journal["phase"] = "rolled_back"
    _save(path, journal)
    print("OTA: restored previous firmware")
    return "rolled_back"


if __name__ == "__main__":
    try:
        if not close_boot_valves():
            raise OSError("An output could not close")
        boot_guard()
        import romboot
        romboot.prepare()
        import sys
        del sys.modules["romboot"]
        del romboot
        import gc
        gc.collect()
    except Exception as error:
        print("Boot recovery failed; refusing application startup:", error)
        # Never run partially recovered code. Reset retries the intact backups.
        import machine
        machine.reset()
