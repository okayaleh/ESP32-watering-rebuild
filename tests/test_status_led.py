"""Exercise LED output, overlapping states and bounded hardware writes."""
import itertools
import os
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from status_led import StatusLED


class Pin:
    OUT = 1

    def __init__(self, number, mode, value=0):
        self.number = number
        self.level = value
        self.writes = []

    def value(self, value):
        self.level = value
        self.writes.append(value)


class Pixel:
    def __init__(self, pin, count):
        self.pin = pin
        self.count = count
        self.color = None
        self.writes = []
        self.fail = False

    def __setitem__(self, index, color):
        if index != 0:
            raise AssertionError("Only the configured status pixel may be changed")
        self.color = color

    def write(self):
        if self.fail:
            raise OSError("injected LED write failure")
        self.writes.append(self.color)


class StatusLEDTests(unittest.TestCase):
    def setUp(self):
        modules = {"machine": types.SimpleNamespace(Pin=Pin),
                   "neopixel": types.SimpleNamespace(NeoPixel=Pixel)}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def make_led(self, mode="rgb", board="esp32", number=2):
        return StatusLED(types.SimpleNamespace(STATUS_LED_PIN=number,
                         STATUS_LED_TYPE=mode, BOARD=board))

    def test_requested_colors_and_all_overlapping_statuses(self):
        led = self.make_led()
        self.assertEqual(led.pin.number, 2)
        self.assertEqual(led.rgb.count, 1)
        previous = None
        transitions = 0
        names = ("watering", "hotspot", "wifi", "grace", "web", "updating")
        for index, flags in enumerate(itertools.product((False, True), repeat=6)):
            state = dict(zip(names, flags))
            with self.subTest(**state):
                expected = ((0, 0, 12) if state["watering"] else
                            (12, 12, 12) if state["hotspot"] else
                            (0, 12, 0) if state["wifi"] else (12, 12, 0))
                led.poll(index * 100, **state)
                self.assertEqual(led.rgb.writes[-1], expected)
                self.assertLessEqual(max(expected), 12)
                transitions += expected != previous
                previous = expected
        self.assertEqual(len(led.rgb.writes), transitions)

    def test_colors_remain_steady_without_redundant_writes(self):
        for state in ({}, {"wifi": True}, {"hotspot": True}, {"watering": True}):
            with self.subTest(**state):
                led = self.make_led()
                for now in range(0, 10000, 10):
                    led.poll(now, **state)
                self.assertEqual(len(led.rgb.writes), 1)

    def test_changes_are_rate_limited_across_tick_wrap(self):
        led = self.make_led()
        wrap = 1 << 30
        led.poll(wrap - 50)
        led.poll(wrap - 1, wifi=True)
        led.poll(0, wifi=True)
        led.poll(49, wifi=True)
        self.assertEqual(led.rgb.writes, [(12, 12, 0)])
        led.poll(50, wifi=True)
        self.assertEqual(led.rgb.writes[-1], (0, 12, 0))
        led.poll(51, watering=True)
        led.poll(149, watering=True)
        self.assertEqual(len(led.rgb.writes), 2)
        led.poll(150, watering=True)
        self.assertEqual(led.rgb.writes[-1], (0, 0, 12))

    def test_failed_write_is_retried_without_caching_success(self):
        led = self.make_led()
        led.rgb.fail = True
        with self.assertRaises(OSError):
            led.poll(0, wifi=True)
        self.assertIsNone(led.output)
        led.rgb.fail = False
        led.poll(100, wifi=True)
        led.poll(200, wifi=True)
        self.assertEqual(led.rgb.writes, [(0, 12, 0)])

    def test_disabled_pin_never_imports_or_accesses_hardware(self):
        with patch.dict(sys.modules, {"machine": None, "neopixel": None}):
            led = self.make_led(number=None)
            led.poll(0, watering=True, hotspot=True)
            led.poll(100, wifi=True)
            self.assertIsNone(led.pin)
            self.assertIsNone(led.rgb)

    def test_plain_led_retains_blink_codes_without_duplicate_writes(self):
        with patch.dict(sys.modules, {"neopixel": None}):
            led = self.make_led(mode="plain", board="esp32s3")
            for now in (0, 100, 200, 300, 400):
                led.poll(now, hotspot=True)
            self.assertEqual(led.pin.writes, [1, 0, 1])
            led.poll(1000, wifi=True, web=False)
            led.poll(1100, wifi=True, web=False)
            led.poll(2000, wifi=True, web=False)
            led.poll(2100, wifi=True, web=True)
            led.poll(3000, wifi=True, web=True)
            self.assertEqual(led.pin.writes, [1, 0, 1, 0, 1])
            self.assertIsNone(led.rgb)

    def test_auto_mode_preserves_board_selection(self):
        self.assertIsNone(self.make_led(mode="auto", board="esp32").rgb)
        self.assertIsNotNone(self.make_led(mode="auto", board="esp32s3").rgb)


if __name__ == "__main__":
    unittest.main()
