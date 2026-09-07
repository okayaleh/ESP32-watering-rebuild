"""Explicit board LED type avoids claiming software can detect an LED electrically."""
from compat import ticks_diff

class StatusLED:
    def __init__(self, config):
        self.pin = self.rgb = None
        number = getattr(config, "STATUS_LED_PIN", None)
        if number is None:
            return
        from machine import Pin
        self.pin = Pin(number, Pin.OUT, value=0)
        mode = getattr(config, "STATUS_LED_TYPE", "auto")
        if mode == "auto":
            mode = "rgb" if getattr(config, "BOARD", "esp32") == "esp32s3" else "plain"
        if mode == "rgb":
            import neopixel
            self.rgb = neopixel.NeoPixel(self.pin, 1)
        self.last = None

    def poll(self, now_ms, watering=False, grace=False, wifi=False, web=False, updating=False):
        if self.pin is None or (self.last is not None and ticks_diff(now_ms, self.last) < 100):
            return
        self.last = now_ms
        if self.rgb:
            color = (0, 6, 0)
            if grace:
                color = (8, 3, 0)
            if watering:
                color = (0, 0, 3 + abs((now_ms // 100) % 24 - 12))
            if not wifi:
                color = (12, 5, 0) if now_ms % 800 < 400 else (0, 0, 0)
            elif not web:
                color = (12, 0, 0) if now_ms % 1600 < 800 else (0, 0, 0)
            if updating:
                color = (8, 0, 12)
            self.rgb[0] = color
            self.rgb.write()
        else:
            self.pin.value(1 if wifi and web else int(now_ms % (400 if not wifi else 2000) < (200 if not wifi else 1000)))
