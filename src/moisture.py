"""Shared-bus recovery and incremental, isolated moisture measurements."""
from compat import ticks_add, ticks_diff
from ads1x15 import ADS1115


def clear_bus(scl_pin, sda_pin, pin_class=None, delay_us=None):
    """Release high as INPUT: never drive into a slave holding a line low."""
    if pin_class is None:
        from machine import Pin
        pin_class = Pin
    if delay_us is None:
        from time import sleep_us
        delay_us = sleep_us
    scl = pin_class(scl_pin, pin_class.IN)
    sda = pin_class(sda_pin, pin_class.IN)

    def release(pin):
        pin.init(pin_class.IN)

    def low(pin):
        pin.init(pin_class.OUT, value=0)

    def clock_high():
        release(scl)
        for _ in range(100):  # Bounded clock stretching, at most 1 ms.
            if scl.value():
                return True
            delay_us(10)
        return False

    try:
        if not clock_high():
            return False
        for _ in range(9):
            if sda.value():
                break
            low(scl)
            delay_us(5)
            if not clock_high():
                return False
            delay_us(5)
        low(scl)
        low(sda)
        delay_us(5)
        if not clock_high():
            return False
        delay_us(5)
        release(sda)  # STOP, SDA rising while SCL is released high.
        delay_us(5)
        return bool(sda.value() and scl.value())
    finally:
        release(scl)
        release(sda)


def create_bus(scl_pin, sda_pin, timeout_us=50000, recover=True):
    from machine import Pin, I2C
    if recover and not clear_bus(scl_pin, sda_pin):
        raise OSError("I2C line held low after bus recovery")
    # Do not silently drop timeout when an unsupported firmware rejects it.
    return I2C(0, scl=Pin(scl_pin), sda=Pin(sda_pin), freq=100000,
               timeout=timeout_us)


def percent(raw, dry, wet):
    if dry == wet:
        raise ValueError("Dry and wet calibration must differ")
    return round(max(0.0, min(100.0, (raw - dry) * 100.0 / (wet - dry))), 1)


class MoistureManager:
    def __init__(self, bus, settings, now_ms=0, interval_ms=15000,
                 probe_ms=60000):
        self.bus = bus
        self.interval_ms = max(1000, interval_ms)
        self.probe_ms = max(1000, probe_ms)
        self.found = []
        self.readings = {}
        self.next_cycle = now_ms
        self.scan_requested = True
        self.pending = None
        self.queue = []
        self.failures = 0
        self.cycle_success = 0
        self.cycle_active = False
        self.calibration_changed = False
        self.calibration = None
        self.calibration_result = None
        self.configure(settings)

    def configure(self, settings):
        self.settings = settings
        hardware = settings.get("hardware", {})
        self.channels = hardware.get("zone_channels", {})
        addresses = hardware.get("ads1115_addresses", [0x48])
        self.boards = [ADS1115(self.bus, addr) for addr in addresses[:4]]
        self.readings = {name: self.readings.get(name, {
            "name": name, "raw": None, "percent": None,
            "error": "Waiting for sensor", "updated_ms": None})
            for name in self.channels}
        self.queue = []
        self.pending = None
        self.cycle_active = False
        self.scan_requested = True

    def request_scan(self):
        self.scan_requested = True

    def start_calibration(self, zone, point, now_ms):
        if self.calibration:
            raise ValueError("Calibration already running")
        if zone not in self.channels or point not in ("dry", "wet"):
            raise ValueError("Unknown zone or calibration point")
        self.calibration = {"zone": zone, "point": point, "start": now_ms,
                            "next": now_ms, "sum": 0, "samples": 0,
                            "min_raw": None, "max_raw": None}
        self.calibration_result = None
        self.scan_requested = True

    def calibration_status(self):
        return {"busy": self.calibration is not None,
                "result": self.calibration_result,
                "calibration": self.settings.get("hardware", {}).get(
                    "zone_calibration", {})}

    def _finish_calibration(self):
        capture = self.calibration
        self.calibration = None
        result = {"zone": capture["zone"], "point": capture["point"],
                  "samples": capture["samples"], "min_raw": capture["min_raw"],
                  "max_raw": capture["max_raw"], "duration_sec": 10,
                  "spread_raw": (capture["max_raw"] - capture["min_raw"])
                      if capture["samples"] else None}
        if capture["samples"] < 3:
            result["error"] = "Not enough valid samples; calibration unchanged"
        else:
            raw = round(capture["sum"] / capture["samples"])
            hardware = self.settings.setdefault("hardware", {})
            calibration = hardware.setdefault("zone_calibration", {})
            old = calibration.get(capture["zone"], {"dry_raw": 17500,
                                                    "wet_raw": 8000})
            other = "wet_raw" if capture["point"] == "dry" else "dry_raw"
            if abs(raw - old.get(other, 8000 if other == "wet_raw" else 17500)) < 100:
                result["error"] = "Calibration span too small; unchanged"
            else:
                value = dict(old)
                value[capture["point"] + "_raw"] = raw
                calibration[capture["zone"]] = value
                result["raw"] = raw
                self.calibration_changed = True
        self.calibration_result = result

    def _error(self, zone, message, now_ms):
        self.readings[zone] = {"name": zone, "raw": None, "percent": None,
                               "error": message, "updated_ms": now_ms}

    def _start(self, zone, now_ms, calibration=False):
        try:
            channel = self.channels[zone]
            if not isinstance(channel, int) or not 0 <= channel < 16:
                raise ValueError("Global channel must be 0..15")
            board = self.boards[channel // 4]
            if board.address not in self.found:
                self._error(zone, "ADS1115 absent", now_ms)
                return
            board.start(channel % 4)
            self.pending = [zone, board, ticks_add(now_ms, 10), now_ms,
                            calibration, False]
        except Exception as exc:
            self._error(zone, str(exc), now_ms)
            # A board disconnected since the scan gets one failed transaction,
            # never another 0.8-second read for each of its remaining channels.
            if "board" in locals() and board.address in self.found:
                self.found.remove(board.address)

    def poll(self, now_ms):
        capture = self.calibration
        if capture and ticks_diff(now_ms, capture["start"]) >= 10000:
            self._finish_calibration()
            capture = None
        if self.pending:
            zone, board, due, start, is_capture, ready = self.pending
            if ticks_diff(now_ms, due) < 0:
                return
            try:
                if not ready:
                    if not board.ready():
                        if ticks_diff(now_ms, start) > 1000:
                            raise OSError("ADC conversion deadline")
                        self.pending[2] = ticks_add(now_ms, 10)
                        return
                    self.pending[5] = True
                    return  # One I2C transaction per poll.
                raw = board.read()
                # Single-ended soil probes cannot legitimately report negative
                # or rail-clipped inputs; never water using an invalid sample.
                if not 0 <= raw < 32760:
                    raise ValueError("Sensor outside measurable range")
                cal = self.settings.get("hardware", {}).get(
                    "zone_calibration", {}).get(zone, {})
                value = percent(raw, cal.get("dry_raw", 17500),
                                cal.get("wet_raw", 8000))
                self.readings[zone] = {"name": zone, "raw": raw,
                    "percent": value, "error": None, "updated_ms": now_ms}
                self.cycle_success += 1
                if is_capture and capture and capture["zone"] == zone:
                    capture["sum"] += raw
                    capture["samples"] += 1
                    capture["min_raw"] = raw if capture["min_raw"] is None else min(raw, capture["min_raw"])
                    capture["max_raw"] = raw if capture["max_raw"] is None else max(raw, capture["max_raw"])
            except Exception as exc:
                self._error(zone, str(exc), now_ms)
                if board.address in self.found:
                    self.found.remove(board.address)
            self.pending = None
            return
        if self.scan_requested:
            self.scan_requested = False
            try:
                self.found = list(self.bus.scan())
            except Exception:
                self.found = []
            return
        if capture and ticks_diff(now_ms, capture["next"]) >= 0:
            capture["next"] = ticks_add(now_ms, 500)
            self._start(capture["zone"], now_ms, True)
            return
        if self.queue:
            self._start(self.queue.pop(0), now_ms)
            return
        if self.cycle_active:
            self.cycle_active = False
            self.failures = 0 if self.cycle_success else min(6, self.failures + 1)
            delay = self.interval_ms if self.cycle_success else min(
                self.probe_ms * 4, self.probe_ms * (2 ** (self.failures - 1)))
            self.next_cycle = ticks_add(now_ms, delay)
            return
        if ticks_diff(now_ms, self.next_cycle) < 0:
            return
        self.cycle_success = 0
        self.cycle_active = True
        self.queue = list(self.channels)
        # Probe before each measurement cycle, including after a total failure.
        # Reading an absent ADC can ignore the platform transaction timeout.
        self.scan_requested = True
