import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from moisture import MoistureManager, clear_bus, percent
from env_sensors import EnvSensors, aht_crc, bmp_compensate


class ElectricalPin:
    IN, OUT = 0, 1
    pins = {}
    pulses = 0
    sda_stuck = True
    scl_stuck = False
    release_at = 9

    def __new__(cls, number, mode=None, value=None):
        if number not in cls.pins:
            cls.pins[number] = super().__new__(cls)
            cls.pins[number].mode = cls.IN
            cls.pins[number].number = number
        return cls.pins[number]

    def __init__(self, number, mode=None, value=None):
        if mode is not None:
            self.init(mode, value=value)

    def init(self, mode, value=None):
        if mode == self.OUT and value != 0:
            raise AssertionError("Push-pull high risks electrical contention")
        if self.number == 22 and mode == self.IN and self.mode == self.OUT:
            type(self).pulses += 1
            if self.pulses >= self.release_at:
                type(self).sda_stuck = False
        self.mode = mode

    def value(self):
        held = self.scl_stuck if self.number == 22 else self.sda_stuck
        return 0 if held or self.mode == self.OUT else 1


class Bus:
    def __init__(self, found=(0x48,), raw=15000):
        self.found = list(found)
        self.raw = raw
        self.writes = []
        self.reads = []
        self.scans = 0
        self.fail = set()

    def scan(self):
        self.scans += 1
        return self.found[:]

    def writeto_mem(self, address, register, data):
        self.writes.append((address, register, data))
        if address not in self.found or address in self.fail:
            raise OSError("Disconnected ADC")

    def readfrom_mem(self, address, register, count):
        self.reads.append((address, register))
        if address not in self.found or address in self.fail:
            raise OSError("Disconnected ADC")
        return b"\x80\x00" if register == 1 else struct.pack(">h", self.raw)


def settings(channels=None):
    return {"hardware": {"zone_channels": channels or {"bed": 0},
                         "ads1115_addresses": [0x48, 0x49, 0x4a, 0x4b],
                         "zone_calibration": {}}}


def drive(sensor, start=0, end=1000, step=20):
    for now in range(start, end, step):
        sensor.poll(now)


class SensorTests(unittest.TestCase):
    def setUp(self):
        ElectricalPin.pins = {}
        ElectricalPin.pulses = 0
        ElectricalPin.sda_stuck = True
        ElectricalPin.scl_stuck = False
        ElectricalPin.release_at = 9

    def test_recovery_never_drives_high_even_when_slave_holds_sda(self):
        self.assertTrue(clear_bus(22, 21, ElectricalPin, lambda us: None))
        self.assertGreaterEqual(ElectricalPin.pulses, 9)
        self.assertTrue(all(pin.mode == ElectricalPin.IN for pin in ElectricalPin.pins.values()))

    def test_clock_stretch_is_bounded_and_lines_released(self):
        ElectricalPin.scl_stuck = True
        sleeps = []
        self.assertFalse(clear_bus(22, 21, ElectricalPin, sleeps.append))
        self.assertEqual(sum(sleeps), 1000)
        self.assertTrue(all(pin.mode == ElectricalPin.IN for pin in ElectricalPin.pins.values()))

    def test_global_channel_15_resolves_fourth_adc_input_3(self):
        bus = Bus((0x4b,))
        sensor = MoistureManager(bus, settings({"far": 15}))
        drive(sensor)
        self.assertEqual(bus.writes[0][0], 0x4b)
        config = int.from_bytes(bus.writes[0][2], "big")
        self.assertEqual((config >> 12) & 7, 7)
        self.assertEqual(sensor.readings["far"]["raw"], 15000)
        self.assertGreaterEqual(bus.scans, 1)

    def test_absent_boards_issue_no_conversions_and_back_off(self):
        bus = Bus(())
        sensor = MoistureManager(bus, settings({"one": 0, "two": 4}),
                                 interval_ms=1000, probe_ms=5000)
        drive(sensor, end=25000)
        self.assertFalse(bus.writes)
        self.assertFalse(bus.reads)
        self.assertLessEqual(bus.scans, 5)
        self.assertGreaterEqual(sensor.failures, 3)
        self.assertTrue(all(value["percent"] is None for value in sensor.readings.values()))

    def test_bad_board_does_not_abort_other_board(self):
        bus = Bus((0x48, 0x49))
        bus.fail.add(0x48)
        sensor = MoistureManager(bus, settings({"bad": 0, "also_bad": 1, "good": 4}))
        drive(sensor)
        self.assertEqual(sum(item[0] == 0x48 for item in bus.writes), 1)
        self.assertIsNotNone(sensor.readings["good"]["percent"])
        self.assertIsNone(sensor.readings["bad"]["percent"])

    def test_one_i2c_transaction_per_poll(self):
        bus = Bus()
        sensor = MoistureManager(bus, settings())
        for now in range(0, 1000, 20):
            before = len(bus.writes) + len(bus.reads) + bus.scans
            sensor.poll(now)
            after = len(bus.writes) + len(bus.reads) + bus.scans
            self.assertLessEqual(after - before, 1)

    def test_calibration_averages_ten_seconds_and_rejects_no_samples(self):
        config = settings()
        sensor = MoistureManager(Bus(raw=15000), config)
        sensor.start_calibration("bed", "dry", 0)
        drive(sensor, end=10000)
        self.assertTrue(sensor.calibration_status()["busy"])
        sensor.poll(10000)
        result = sensor.calibration_status()["result"]
        self.assertEqual(result["raw"], 15000)
        self.assertGreaterEqual(result["samples"], 10)
        self.assertEqual(config["hardware"]["zone_calibration"]["bed"]["dry_raw"], 15000)
        sensor = MoistureManager(Bus(()), config)
        sensor.start_calibration("bed", "wet", 0)
        drive(sensor, end=10020)
        self.assertIn("error", sensor.calibration_status()["result"])
        self.assertEqual(config["hardware"]["zone_calibration"]["bed"]["wet_raw"], 8000)

    def test_percentage_works_for_both_sensor_polarities(self):
        self.assertEqual(percent(12500, 17500, 7500), 50)
        self.assertEqual(percent(12500, 7500, 17500), 50)
        with self.assertRaises(ValueError):
            percent(123, 100, 100)

    def test_bmp_datasheet_reference_vector(self):
        calibration = (27504, 26435, -1000, 36477, -10685, 3024,
                       2855, 140, -7, 15500, -14600, 6000)
        data = bytes((0x65, 0x5a, 0xc0, 0x7e, 0xed, 0x00))
        temperature, pressure = bmp_compensate(data, calibration)
        self.assertAlmostEqual(temperature, 25.08, places=2)
        self.assertAlmostEqual(pressure, 100653.27, places=2)

    def test_absent_environment_never_reads_bus(self):
        bus = Bus(())
        sensors = EnvSensors(bus, [])
        drive(sensors)
        self.assertFalse(bus.reads)
        self.assertFalse(bus.writes)

    def test_aht_crc_vector(self):
        # Sensirion polynomial 0x31, initial 0xff, also used by AHT20.
        self.assertEqual(aht_crc(bytes((0xbe, 0xef))), 0x92)


if __name__ == "__main__":
    unittest.main()
