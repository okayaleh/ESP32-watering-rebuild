"""One normally closed solenoid; logical state changes only after GPIO writes."""
from compat import ticks_diff

class Valve:
    def __init__(self, spec, pin_factory=None):
        if pin_factory is None:
            from machine import Pin
            pin_factory = Pin
        self.name = spec["name"]
        self.closed_level = 0 if spec.get("active_high", True) else 1
        self.pin = pin_factory(spec["pin"], pin_factory.OUT, value=self.closed_level)
        self.is_open = False
        self.opened_ms = None
        self.duration_ms = 0
        self.reason = None
        self.last_close_reason = None

    def open(self, now_ms, duration_sec, reason):
        if self.is_open:
            raise RuntimeError("Valve is already open")
        # Record intent BEFORE writing: an exception after energizing still
        # leaves enough state for the controller to attempt closure.
        self.is_open = True
        self.opened_ms = now_ms
        self.duration_ms = int(duration_sec * 1000)
        self.reason = reason
        self.pin.value(1 - self.closed_level)

    def close(self, reason="stopped"):
        self.pin.value(self.closed_level)
        self.is_open = False
        self.opened_ms = None
        self.last_close_reason = reason

    def expired(self, now_ms, maximum_sec):
        return self.is_open and ticks_diff(now_ms, self.opened_ms) >= min(self.duration_ms, maximum_sec * 1000)
