"""Small ADS1115 single-shot driver; conversion waiting belongs to the loop."""


class ADS1115:
    CONVERSION_MS = 10  # 128 samples/sec, including oscillator tolerance.

    def __init__(self, bus, address=0x48):
        if address not in (0x48, 0x49, 0x4a, 0x4b):
            raise ValueError("Invalid ADS1115 address")
        self.bus = bus
        self.address = address

    def start(self, channel):
        if not isinstance(channel, int) or not 0 <= channel <= 3:
            raise ValueError("ADS channel must be 0..3")
        # OS=start, single-ended mux, +/-4.096 V, single-shot, 128 SPS,
        # comparator disabled. Analog inputs must still remain within supply.
        config = 0x8000 | ((4 + channel) << 12) | 0x0200 | 0x0100 | 0x0083
        self.bus.writeto_mem(self.address, 1,
                            bytes((config >> 8, config & 255)))

    def ready(self):
        return bool(self.bus.readfrom_mem(self.address, 1, 2)[0] & 0x80)

    def read(self):
        data = self.bus.readfrom_mem(self.address, 0, 2)
        raw = (data[0] << 8) | data[1]
        return raw - 65536 if raw & 0x8000 else raw
