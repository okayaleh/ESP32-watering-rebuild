"""Explicit board LED type avoids claiming software can detect an LED electrically."""
from compat import ticks_diff

class StatusLED:
    def __init__(self, config):
        self.pin = self.rgb = None
        self.last = self.output = None
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

    def poll(self, now_ms, watering=False, grace=False, wifi=False, web=False, updating=False, hotspot=False):
        if self.pin is None or (self.last is not None and ticks_diff(now_ms, self.last) < 100):
            return
        self.last = now_ms
        if self.rgb is not None:
            # Keep the requested status visible during grace, update and web
            # faults. A rescue AP can coexist with a connected station.
            # At most 12/255 per channel bounds brightness and current draw.
            if watering:
                output = (0, 0, 12)
            elif hotspot:
                output = (12, 12, 12)
            elif wifi:
                output = (0, 12, 0)
            else:
                output = (12, 12, 0)
            if output != self.output:
                self.rgb[0] = output
                self.rgb.write()
                self.output = output
        else:
            # Preserve the blink codes for boards with a single-color LED.
            output = 1 if wifi and web else int(now_ms % (400 if not wifi else 2000) < (200 if not wifi else 1000))
            if output != self.output:
                self.pin.value(output)
                self.output = output
