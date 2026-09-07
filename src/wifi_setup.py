"""Runtime rescue access point and bounded captive DNS responder."""
try:
    import usocket as socket
except ImportError:
    import socket
import network


class RescuePortal:
    def __init__(self, event=None):
        self.event = event
        self.ap = network.WLAN(network.AP_IF)
        self.dns = None
        self.active = False
        self.ssid = 'Planter-Setup'
        self.ip = '192.168.4.1'
        self.error = None

    def start(self):
        if self.active:
            return True
        try:
            try:
                import machine
                import binascii
                self.ssid = 'Planter-Setup-' + binascii.hexlify(machine.unique_id()[-2:]).decode()
            except (ImportError, AttributeError):
                self.ssid = 'Planter-Setup'
            self.ap.active(False)
            self.ap.active(True)
            # Setting an inactive AP is invalid on ESP32. Bounce after applying
            # all security fields together so DHCP uses the final open config.
            try:
                self.ap.config(essid=self.ssid, password='', authmode=network.AUTH_OPEN)
            except (ValueError, TypeError):
                self.ap.config(ssid=self.ssid, key='', security=network.AUTH_OPEN)
            self.ap.active(False)
            self.ap.active(True)
            self.ap.ifconfig((self.ip, '255.255.255.0', self.ip, self.ip))
            self.active = True
            try:
                self.dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.dns.setblocking(False)
                self.dns.bind(('0.0.0.0', 53))
            except OSError as exc:
                # The AP and direct dashboard remain useful if DNS bind fails.
                self.error = str(exc)[:120]
                if self.dns:
                    self.dns.close()
                self.dns = None
            if self.event:
                self.event('network', 'Rescue hotspot ' + self.ssid + ' at ' + self.ip)
            return True
        except Exception as exc:
            self.error = str(exc)[:120]
            self.close()
            return False

    def close(self):
        if self.dns:
            try:
                self.dns.close()
            except OSError:
                pass
        self.dns = None
        try:
            self.ap.active(False)
        except OSError:
            pass
        self.active = False

    @staticmethod
    def dns_answer(packet):
        if len(packet) < 17 or packet[2] & 0x80 or packet[4:6] != b'\x00\x01':
            return None
        cursor = 12
        while cursor < len(packet):
            size = packet[cursor]
            cursor += 1
            if size == 0:
                break
            if size > 63 or cursor + size > len(packet):
                return None
            cursor += size
        if cursor + 4 > len(packet) or cursor > 267:
            return None
        question = packet[12:cursor + 4]
        is_a = packet[cursor:cursor + 4] == b'\x00\x01\x00\x01'
        header = packet[:2] + b'\x81\x80\x00\x01' + (b'\x00\x01' if is_a else b'\x00\x00') + b'\x00\x00\x00\x00'
        answer = b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x1e\x00\x04\xc0\xa8\x04\x01'
        return header + question + (answer if is_a else b'')

    def poll(self, now_ms=None):
        if not self.dns:
            return
        try:
            packet, address = self.dns.recvfrom(512)
            answer = self.dns_answer(packet)
            if answer:
                self.dns.sendto(answer, address)
        except OSError as exc:
            if exc.args and exc.args[0] not in (11, 35, 10035):
                self.error = str(exc)[:120]

    def status(self):
        return {'active': self.active, 'ssid': self.ssid, 'ip': self.ip,
                'dns': self.dns is not None, 'error': self.error}
