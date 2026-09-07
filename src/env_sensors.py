"""AHT20, BMP280 and configured digital rain input on the shared bus.

No conversion sleeps: each poll issues at most one I2C transaction. Sensor
failure removes that device until the next interval without affecting ADS.
"""
import struct
from compat import ticks_add, ticks_diff


def aht_crc(data):
    crc = 0xff
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 255 if crc & 0x80 else (crc << 1) & 255
    return crc


def bmp_compensate(data, calibration):
    t1, t2, t3, p1, p2, p3, p4, p5, p6, p7, p8, p9 = calibration
    praw = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
    traw = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
    if praw == 0x80000 or traw == 0x80000:
        raise ValueError("BMP280 sample unavailable")
    tfine = ((traw / 16384.0 - t1 / 1024.0) * t2 +
             (traw / 131072.0 - t1 / 8192.0) ** 2 * t3)
    v1 = tfine / 2.0 - 64000.0
    v2 = v1 * v1 * p6 / 32768.0 + v1 * p5 * 2.0
    v2 = v2 / 4.0 + p4 * 65536.0
    v1 = (p3 * v1 * v1 / 524288.0 + p2 * v1) / 524288.0
    v1 = (1.0 + v1 / 32768.0) * p1
    if not v1:
        raise ValueError("BMP280 calibration invalid")
    pressure = (1048576.0 - praw - v2 / 4096.0) * 6250.0 / v1
    pressure += (p9 * pressure * pressure / 2147483648.0 +
                 pressure * p8 / 32768.0 + p7) / 16.0
    return tfine / 5120.0, pressure


class EnvSensors:
    def __init__(self, bus, present, rain_pin=None, now_ms=0,
                 interval_ms=30000, pin_class=None):
        self.bus = bus
        self.present = present
        self.interval_ms = max(1000, interval_ms)
        self.next_cycle = now_ms
        self.queue = []
        self.calibration = {}
        self.readings = {"temperature_c": None, "humidity_percent": None,
                         "pressure_hpa": None, "rain": None, "errors": {}}
        self.rain_pin = None
        if rain_pin is not None:
            if pin_class is None:
                from machine import Pin
                pin_class = Pin
            self.rain_pin = pin_class(rain_pin, pin_class.IN)

    def poll(self, now_ms):
        if not self.queue:
            if ticks_diff(now_ms, self.next_cycle) < 0:
                return
            self.next_cycle = ticks_add(now_ms, self.interval_ms)
            found = self.present() if callable(self.present) else self.present
            self.readings["errors"] = {}
            if self.rain_pin:
                try:
                    self.readings["rain"] = not bool(self.rain_pin.value())
                except Exception as exc:
                    self.readings["rain"] = None
                    self.readings["errors"]["rain"] = str(exc)
            if 0x38 in found:
                self.queue.append([0x38, "aht_status", now_ms])
            else:
                self.readings["humidity_percent"] = None
                self.readings["temperature_c"] = None
            for addr in (0x76, 0x77):
                if addr in found:
                    stage = "bmp_start" if addr in self.calibration else "bmp_id"
                    self.queue.append([addr, stage, now_ms])
                    break
            else:
                self.readings["pressure_hpa"] = None
                self.readings["bmp_temperature_c"] = None
            return
        addr, stage, due = self.queue[0]
        if ticks_diff(now_ms, due) < 0:
            return
        found = self.present() if callable(self.present) else self.present
        if addr not in found:
            self.queue.pop(0)
            return
        try:
            if stage == "aht_status":
                status = self.bus.readfrom(addr, 1)[0]
                stage = "aht_start" if status & 8 else "aht_init"
            elif stage == "aht_init":
                self.bus.writeto(addr, b"\xbe\x08\x00")
                stage, due = "aht_start", ticks_add(now_ms, 10)
            elif stage == "aht_start":
                self.bus.writeto(addr, b"\xac\x33\x00")
                stage, due = "aht_read", ticks_add(now_ms, 85)
            elif stage == "aht_read":
                data = self.bus.readfrom(addr, 7)
                if len(data) != 7 or data[0] & 0x80 or aht_crc(data[:6]) != data[6]:
                    raise ValueError("AHT20 busy or CRC error")
                humidity = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
                temperature = ((data[3] & 15) << 16) | (data[4] << 8) | data[5]
                self.readings["humidity_percent"] = round(humidity * 100 / 1048576, 1)
                self.readings["temperature_c"] = round(temperature * 200 / 1048576 - 50, 1)
                stage = None
            elif stage == "bmp_id":
                if self.bus.readfrom_mem(addr, 0xd0, 1)[0] != 0x58:
                    raise ValueError("Device is not a BMP280")
                stage = "bmp_cal"
            elif stage == "bmp_cal":
                data = self.bus.readfrom_mem(addr, 0x88, 24)
                self.calibration[addr] = struct.unpack("<HhhHhhhhhhhh", data)
                stage = "bmp_start"
            elif stage == "bmp_start":
                self.bus.writeto_mem(addr, 0xf4, b"\x25")  # x1, forced mode
                stage, due = "bmp_read", ticks_add(now_ms, 10)
            elif stage == "bmp_read":
                data = self.bus.readfrom_mem(addr, 0xf7, 6)
                temperature, pressure = bmp_compensate(data, self.calibration[addr])
                self.readings["bmp_temperature_c"] = round(temperature, 1)
                self.readings["pressure_hpa"] = round(pressure / 100, 1)
                stage = None
            if stage is None:
                self.queue.pop(0)
            else:
                self.queue[0] = [addr, stage, due]
        except Exception as exc:
            self.queue.pop(0)
            sensor = "aht20" if addr == 0x38 else "bmp280"
            self.readings["errors"][sensor] = str(exc)
            if addr == 0x38:
                self.readings["temperature_c"] = None
                self.readings["humidity_percent"] = None
            else:
                self.calibration.pop(addr, None)
                self.readings["pressure_hpa"] = None
                self.readings["bmp_temperature_c"] = None
