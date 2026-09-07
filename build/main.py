# Boot entry stays tiny: modules are precompiled, leaving RAM for WiFi/lwIP.
import machine
import gc
gc.collect()
if hasattr(gc, "threshold"):
    gc.threshold(4096)
import json
import config

_valves = config.VALVES
for _path in ("settings.json", "settings.json.prev"):
    try:
        with open(_path) as _file:
            _valves = json.load(_file)["hardware"]["valves"]
        break
    except (OSError, ValueError, KeyError, TypeError):
        pass
_close_failed = False
for _valve in _valves:
    try:
        machine.Pin(_valve["pin"], machine.Pin.OUT, value=0 if _valve.get("active_high", True) else 1)
    except Exception:
        _close_failed = True
if _close_failed:
    machine.reset()
print("Reset cause:", machine.reset_cause())
_maintenance = False
try:
    import runtime
    runtime.run(config)
except KeyboardInterrupt:
    _maintenance = True
finally:
    _close_failed = False
    for _valve in _valves:
        try:
            machine.Pin(_valve["pin"], machine.Pin.OUT, value=0 if _valve.get("active_high", True) else 1)
        except Exception:
            _close_failed = True
    if _maintenance and not _close_failed:
        print("USB maintenance: outputs closed; watchdog timeout still applies.")
    else:
        machine.reset()
