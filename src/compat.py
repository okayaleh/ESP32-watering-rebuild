"""Small shared portability layer; all intervals use wrap-safe milliseconds."""
import time
try:
    import ujson as json
except ImportError:
    import json

try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
    ticks_add = time.ticks_add
except AttributeError:
    _PERIOD = 1 << 30
    def ticks_ms():
        return int(time.monotonic() * 1000) % _PERIOD
    def ticks_diff(a, b):
        return ((a - b + _PERIOD // 2) % _PERIOD) - _PERIOD // 2
    def ticks_add(a, b):
        return (a + b) % _PERIOD

# Public/persisted timestamps always use Unix seconds, including ports whose
# native RTC epoch is 2000. Safety clocks remain independent.
EPOCH_OFFSET = 946684800 if time.gmtime(0)[0] == 2000 else 0

def epoch():
    return int(time.time()) + EPOCH_OFFSET
