"""Nonblocking WiFi recovery, DNS, gateway probes and clock synchronization.

No getaddrinfo(), sleeps, settimeout(), or synchronous NTP helpers run in the
controller loop. Link association is never presented as proof of HTTP health.
"""
try:
    import usocket as socket
except ImportError:
    import socket
try:
    import uselect as select
except ImportError:
    import select
import network
import time
from compat import ticks_ms, ticks_diff, ticks_add
from persistence import read_json, atomic_json
from wifi_setup import RescuePortal

AGAIN = (11, 35, 10035)
IN_PROGRESS = AGAIN + (115, 114, 119, 120, 10036, 10037)
REPLY_ERRORS = (104, 111, 54, 61, 10054, 10061)
CONNECTED_ERRORS = (106, 127, 56, 10056)
TIMEOUT_ERRORS = (110, 116, 60, 10060)


def _code(exc):
    return exc.args[0] if exc.args else None


def ipv4(host):
    if not isinstance(host, str):
        return False
    parts = host.split('.')
    return len(parts) == 4 and all(part.isdigit() and len(part) <= 3 and 0 <= int(part) <= 255 for part in parts)


def _close(sock):
    if sock:
        try:
            sock.close()
        except OSError:
            pass


def _skip_name(packet, offset):
    for unused in range(128):
        if offset >= len(packet):
            raise ValueError('Truncated DNS name')
        size = packet[offset]
        offset += 1
        if size == 0:
            return offset
        if size & 192 == 192:
            if offset >= len(packet):
                raise ValueError('Truncated DNS pointer')
            return offset + 1
        if size > 63:
            raise ValueError('Invalid DNS label')
        offset += size
    raise ValueError('DNS name too long')


class DNSResolver:
    def __init__(self, server='192.168.1.1'):
        self.server = server
        self.cache = {}
        self.queue = []
        self.sock = None
        self.host = None
        self.query = None
        self.question = None
        self.txid = 0
        self.deadline = None
        self.now = ticks_ms()
        self.error = None

    def resolve(self, host):
        if ipv4(host):
            return host
        if not isinstance(host, str) or not host or len(host) > 253:
            raise ValueError('Invalid DNS hostname')
        host = host.rstrip('.').lower()
        cached = self.cache.get(host)
        if cached and ticks_diff(self.now, cached[1]) < 0:
            return cached[0]
        if host != self.host and host not in self.queue and len(self.queue) < 4:
            self.queue.append(host)
        return None

    def close(self):
        _close(self.sock)
        self.sock = None
        self.host = None
        self.query = None

    def _remember(self, address, ttl):
        if len(self.cache) >= 8 and self.host not in self.cache:
            del self.cache[next(iter(self.cache))]
        self.cache[self.host] = (address, ticks_add(self.now, max(5, min(ttl, 3600)) * 1000))
        self.close()

    def _start(self):
        self.host = self.queue.pop(0)
        try:
            self.txid = (self.txid + 173) & 65535
            question = bytearray()
            for label in self.host.split('.'):
                encoded = label.encode('ascii')
                if not encoded or len(encoded) > 63:
                    raise ValueError('Invalid DNS label')
                question.append(len(encoded))
                question.extend(encoded)
            question.extend(b'\x00\x00\x01\x00\x01')
            self.question = bytes(question)
            self.query = bytes((self.txid >> 8, self.txid & 255)) + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00' + self.question
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
            self.deadline = ticks_add(self.now, 3000)
        except Exception as exc:
            self.error = str(exc)[:120]
            self._remember(None, 30)

    def _answer(self, packet):
        if len(packet) < 12 or ((packet[0] << 8) | packet[1]) != self.txid:
            return False
        if not packet[2] & 128 or packet[2] & 2 or packet[4:6] != b'\x00\x01':
            return False
        question_end = _skip_name(packet, 12) + 4
        if packet[12:question_end] != self.question:
            return False
        if packet[3] & 15:
            self._remember(None, 30)
            return True
        offset = question_end
        answers = (packet[6] << 8) | packet[7]
        for unused in range(min(answers, 16)):
            offset = _skip_name(packet, offset)
            if offset + 10 > len(packet):
                return False
            record_type = (packet[offset] << 8) | packet[offset + 1]
            record_class = (packet[offset + 2] << 8) | packet[offset + 3]
            ttl = int.from_bytes(packet[offset + 4:offset + 8], 'big')
            size = (packet[offset + 8] << 8) | packet[offset + 9]
            offset += 10
            if offset + size > len(packet):
                return False
            if record_type == 1 and record_class == 1 and size == 4:
                self._remember('.'.join(str(part) for part in packet[offset:offset + 4]), ttl)
                return True
            offset += size
        self._remember(None, 30)
        return True

    def poll(self, now_ms=None, allow_start=True):
        self.now = ticks_ms() if now_ms is None else now_ms
        if self.sock is None and self.queue and allow_start and ipv4(self.server):
            self._start()
        if self.sock is None:
            return
        if ticks_diff(self.now, self.deadline) >= 0:
            self.error = 'DNS timeout'
            self._remember(None, 30)
            return
        try:
            if self.query:
                sent = self.sock.sendto(self.query, (self.server, 53))
                if sent == len(self.query):
                    self.query = None
                return
            packet, source = self.sock.recvfrom(512)
            if source[0] == self.server and source[1] == 53:
                self._answer(packet)
        except OSError as exc:
            if _code(exc) not in AGAIN:
                self.error = str(exc)[:120]
                self._remember(None, 30)
        except (ValueError, IndexError):
            pass


class GatewayProbe:
    """A refusal/reset proves LAN reachability. Only deadline expiry fails."""
    def __init__(self, timeout_ms=3000):
        self.timeout_ms = timeout_ms
        self.sock = None
        self.poller = None
        self.deadline = None
        self.result = None
        self.address = None

    def close(self):
        if self.poller and self.sock:
            try:
                self.poller.unregister(self.sock)
            except (OSError, KeyError):
                pass
        _close(self.sock)
        self.sock = None
        self.poller = None

    def _done(self, result):
        self.result = result
        self.close()
        return result

    def start(self, gateway, now):
        self.close()
        self.result = None
        if not ipv4(gateway) or gateway == '0.0.0.0':
            return self._done('inconclusive')
        self.address = (gateway, 80)
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setblocking(False)
            self.deadline = ticks_add(now, self.timeout_ms)
            self.poller = select.poll()
            self.poller.register(self.sock, select.POLLOUT | select.POLLERR | select.POLLHUP)
            self.sock.connect(self.address)
            return self._done('healthy')
        except OSError as exc:
            if _code(exc) in REPLY_ERRORS + CONNECTED_ERRORS:
                return self._done('healthy')
            if _code(exc) in TIMEOUT_ERRORS:
                return self._done('timeout')
            if _code(exc) not in IN_PROGRESS:
                return self._done('inconclusive')
        except Exception:
            return self._done('inconclusive')
        return None

    def poll(self, now):
        if self.sock is None:
            return self.result
        if ticks_diff(now, self.deadline) >= 0:
            return self._done('timeout')
        try:
            ready = self.poller.poll(0)
            if not ready:
                return None
            # SO_ERROR distinguishes connect completion from async refusal.
            # Some MicroPython ports lack it; inspect a bounded read first.
            if hasattr(socket, 'SO_ERROR') and hasattr(self.sock, 'getsockopt'):
                error = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error == 0 or error in REPLY_ERRORS + CONNECTED_ERRORS:
                    return self._done('healthy')
                if error in TIMEOUT_ERRORS:
                    return self._done('timeout')
                if error in IN_PROGRESS:
                    return None
                return self._done('inconclusive')
            # ESP32 lwIP deletes the PCB after an async reset: reconnect then
            # reports only ENOTCONN. recv preserves the queued reset/refusal.
            # Data or EOF also proves a peer replied; EAGAIN alone does not.
            try:
                self.sock.recv(1)
                return self._done('healthy')
            except OSError as exc:
                if _code(exc) not in AGAIN:
                    raise
            # With no queued data/error, confirm the writable connection.
            self.sock.connect(self.address)
            return self._done('healthy')
        except OSError as exc:
            if _code(exc) in REPLY_ERRORS + CONNECTED_ERRORS:
                return self._done('healthy')
            if _code(exc) in TIMEOUT_ERRORS:
                return self._done('timeout')
            if _code(exc) not in IN_PROGRESS:
                return self._done('inconclusive')
        return None


class WifiManager:
    def __init__(self, config, credential_path='wifi.json', event=None):
        self.config = config
        self.credential_path = credential_path
        self.event = event
        saved = read_json(credential_path, None)
        self.saved_credentials = isinstance(saved, dict) and bool(saved.get('ssid'))
        self.credentials = saved if self.saved_credentials else {
            'ssid': getattr(config, 'WIFI_SSID', ''), 'password': getattr(config, 'WIFI_PASSWORD', '')}
        self.wlan = network.WLAN(network.STA_IF)
        self.portal = RescuePortal(event)
        self.dns = DNSResolver()
        self.probe = GatewayProbe(int(getattr(config, 'WIFI_HEALTH_TIMEOUT_SEC', 3) * 1000))
        self.connected = False
        self.state = 'offline'
        self.last_error = None
        self.down_since = ticks_ms()
        self.next_attempt = self.down_since
        self.connect_deadline = None
        self.next_health = ticks_add(self.down_since, 60000)
        self.backoff_ms = 1000
        self.failures = 0
        self.health_failures = 0
        self.health_result = 'unverified'
        self.ever_connected = False

    def _event(self, message):
        if self.event:
            try:
                self.event('network', message)
            except Exception:
                pass

    def save_credentials(self, ssid, password):
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
            raise ValueError('SSID must contain 1 to 32 UTF-8 bytes')
        if not isinstance(password, str) or len(password.encode()) > 63:
            raise ValueError('WiFi password must be at most 63 UTF-8 bytes')
        data = {'ssid': ssid, 'password': password}
        atomic_json(self.credential_path, data)
        if read_json(self.credential_path, None) != data:
            raise OSError('WiFi credential read-back failed')
        return True

    def _park(self):
        try:
            if not self.wlan.active():
                return
        except (OSError, RuntimeError):
            pass
        try:
            if self.state == 'connecting' or self.wlan.isconnected():
                self.wlan.disconnect()
        except (OSError, RuntimeError):
            # ESP32 maps unknown IDF results (including ESP_FAIL) to
            # RuntimeError. A failed disconnect must not skip deactivation.
            pass
        try:
            self.wlan.active(False)
        except (OSError, RuntimeError):
            pass

    def _rescue(self, now):
        self._park()
        # Failed AP startup must also back off instead of retrying each slice.
        self.next_attempt = ticks_add(now, 60000)
        if self.portal.start():
            self.state = 'rescue'

    def _connect(self, now, hard=False):
        if not self.credentials.get('ssid'):
            self._rescue(now)
            return
        try:
            try:
                network.hostname('planter')
            except AttributeError:
                try:
                    self.wlan.config(dhcp_hostname='planter')
                except (OSError, RuntimeError, ValueError, TypeError):
                    pass
            if hard:
                self._park()
            self.wlan.active(True)
            try:
                self.wlan.config(reconnects=0)
            except (OSError, RuntimeError, ValueError):
                self._event('WiFi retry configuration unavailable')
            if not getattr(self.config, 'WIFI_POWER_SAVE', False):
                try:
                    self.wlan.config(pm=getattr(network.WLAN, 'PM_NONE', 0))
                except (OSError, RuntimeError, ValueError):
                    self._event('WiFi power-save configuration unavailable')
            self.wlan.connect(self.credentials['ssid'], self.credentials.get('password', ''))
            self.connect_deadline = ticks_add(now, 12000 if self.portal.active else 20000)
            self.state = 'connecting'
        except (OSError, RuntimeError) as exc:
            self.last_error = str(exc)[:120]
            self._failed(now)

    def _failed(self, now, status=None):
        if status is not None:
            if status == getattr(network, 'STAT_NO_AP_FOUND', -2):
                reason = 'WiFi network not found'
            elif status == getattr(network, 'STAT_WRONG_PASSWORD', -3):
                reason = 'WiFi authentication failed'
            elif status == getattr(network, 'STAT_CONNECT_FAIL', -1):
                reason = 'WiFi connection failed'
            else:
                reason = 'WiFi connection timed out'
            self.last_error = ('%s (status %s)' % (reason, status))[:120]
        self.failures += 1
        self._park()
        self.state = 'rescue' if self.portal.active else 'offline'
        self.next_attempt = ticks_add(now, 60000 if self.portal.active else self.backoff_ms)
        self.backoff_ms = min(self.backoff_ms * 2, 60000)
        self._event('WiFi connection failed; retry scheduled')
        wrong_password = getattr(network, 'STAT_WRONG_PASSWORD', -3)
        if not self.ever_connected and (self.saved_credentials or status == wrong_password):
            self._rescue(now)

    def _link_health(self, now, valve_open):
        if valve_open:
            if self.probe.sock:
                self.probe.close()
                self.next_health = ticks_add(now, 30000)
            return
        if self.probe.sock:
            result = self.probe.poll(now)
        elif ticks_diff(now, self.next_health) >= 0:
            result = self.probe.start(self.wlan.ifconfig()[2], now)
        else:
            return
        if result is None:
            return
        self.health_result = result
        interval = int(getattr(self.config, 'WIFI_HEALTH_CHECK_SEC', 900) * 1000)
        self.next_health = ticks_add(now, max(30000, interval))
        if result == 'healthy':
            self.health_failures = 0
        elif result == 'timeout':
            self.health_failures += 1
            self.next_health = ticks_add(now, 30000)
            self._event('Gateway probe timed out (%d)' % self.health_failures)
            if self.health_failures >= 2:
                self.connected = False
                self.down_since = now
                if self.health_failures == 2:
                    self.wlan.disconnect()
                self._connect(now, hard=self.health_failures >= 3)

    def poll(self, now_ms=None, valve_open=False):
        now = ticks_ms() if now_ms is None else now_ms
        try:
            if not self.credentials.get('ssid'):
                # No station association is possible until provisioning saves
                # credentials and reboots. Keep serving captive DNS without
                # disconnecting an unconfigured STA every minute. A temporary
                # STA activation for a setup scan is also left undisturbed.
                if (not self.portal.active and not valve_open and
                        ticks_diff(now, self.next_attempt) >= 0):
                    self._rescue(now)
                self.portal.poll(now)
                return
            linked = self.wlan.isconnected()
            if linked:
                if not self.connected:
                    self.connected = True
                    self.ever_connected = True
                    self.state = 'connected'
                    self.last_error = None
                    self.failures = 0
                    self.backoff_ms = 1000
                    self.next_health = ticks_add(now, 30000 if self.health_failures else 60000)
                    self.portal.close()
                    self._event('WiFi connected: ' + self.wlan.ifconfig()[0])
                self.dns.server = self.wlan.ifconfig()[3]
                self.dns.poll(now, allow_start=not valve_open)
                self._link_health(now, valve_open)
            else:
                if self.connected:
                    self.connected = False
                    self.down_since = now
                    self.next_attempt = now
                    self.state = 'offline'
                    self.probe.close()
                    self.dns.close()
                    self._event('WiFi disconnected; watering remains active')
                if self.state == 'connecting':
                    status = self.wlan.status()
                    # ESP32 retains the previous attempt's failure reason until
                    # GOT_IP. Give the new attempt its full deadline even when
                    # status still reports that old authentication/AP error.
                    if ticks_diff(now, self.connect_deadline) >= 0:
                        self._failed(now, status)
                elif ticks_diff(now, self.next_attempt) >= 0 and not valve_open:
                    self._connect(now)
                rescue_ms = int(getattr(self.config, 'WIFI_RESCUE_AFTER_SEC', 300) * 1000)
                if rescue_ms > 0 and not self.portal.active and ticks_diff(now, self.down_since) >= rescue_ms and not valve_open:
                    self._rescue(now)
            self.portal.poll(now)
        except (OSError, RuntimeError) as exc:
            self.last_error = str(exc)[:120]
            self._event('WiFi poll error: ' + self.last_error)

    def status(self):
        result = {'ssid': self.credentials.get('ssid', ''), 'connected': self.connected,
                  'state': self.state, 'ip': None, 'rssi': None,
                  'last_error': self.last_error, 'gateway_health': self.health_result,
                  'lan_ok': True if self.health_result == 'healthy' else (False if self.health_result == 'timeout' else None),
                  'reconnect_failures': self.failures, 'portal': self.portal.status()}
        if self.connected:
            try:
                result['ip'] = self.wlan.ifconfig()[0]
                result['rssi'] = self.wlan.status('rssi')
            except (OSError, RuntimeError) as exc:
                self.last_error = str(exc)[:120]
                result['last_error'] = self.last_error
                result['ip'] = result['rssi'] = None
        return result


class NTPClient:
    def __init__(self, config=None, event=None, resolver=None):
        self.config = config
        self.event = event
        self.resolver = resolver or DNSResolver()
        self.owns_resolver = resolver is None
        self.host = getattr(config, 'NTP_HOST', 'pool.ntp.org')
        self.sock = None
        self.address = None
        self.packet = None
        self.cookie = None
        self.deadline = None
        self.next_attempt = ticks_ms()
        self.synced = False
        self.last_sync = None
        self.last_sync_ms = None
        self.error = None

    def _finish(self, now, success=False):
        _close(self.sock)
        self.sock = None
        self.packet = None
        delay = int(getattr(self.config, 'NTP_RESYNC_SEC', 3600)) if success else 60
        self.next_attempt = ticks_add(now, max(30, delay) * 1000)

    def poll(self, now_ms=None, connected=True, valve_open=False):
        now = ticks_ms() if now_ms is None else now_ms
        if not connected or valve_open:
            if self.sock:
                self._finish(now)
            return False
        if self.owns_resolver:
            self.resolver.poll(now)
        if self.sock is None:
            if ticks_diff(now, self.next_attempt) < 0:
                return False
            self.address = self.resolver.resolve(self.host)
            if not self.address:
                return False
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setblocking(False)
                self.packet = bytearray(48)
                self.packet[0] = 0x23  # LI=0, version=4, client mode.
                try:
                    import os
                    self.cookie = os.urandom(8)
                except (AttributeError, OSError):
                    self.cookie = now.to_bytes(8, 'big')
                self.packet[40:48] = self.cookie
                self.deadline = ticks_add(now, 3000)
            except OSError as exc:
                self.error = str(exc)[:120]
                self._finish(now)
                return False
        if ticks_diff(now, self.deadline) >= 0:
            self.error = 'NTP timeout'
            self._finish(now)
            return False
        try:
            if self.packet:
                if self.sock.sendto(self.packet, (self.address, 123)) == 48:
                    self.packet = None
                return False
            packet, source = self.sock.recvfrom(512)
            if source != (self.address, 123) or len(packet) < 48:
                return False
            if packet[0] >> 6 == 3 or packet[0] & 7 != 4 or ((packet[0] >> 3) & 7) not in (3, 4) or not 1 <= packet[1] <= 15:
                return False
            if packet[24:32] != self.cookie:
                return False
            seconds = int.from_bytes(packet[40:44], 'big')
            # NTP era zero rolls over in 2036; current supported dates are 2024+
            # and era one is unambiguous for this product's operating lifetime.
            if seconds < 0x80000000:
                seconds += 4294967296
            offset = 3155673600 if time.gmtime(0)[0] == 2000 else 2208988800
            timestamp = seconds - offset
            stamp = time.gmtime(timestamp)
            if not 2024 <= stamp[0] <= 2099:
                return False
            import machine
            machine.RTC().datetime((stamp[0], stamp[1], stamp[2], stamp[6], stamp[3], stamp[4], stamp[5], 0))
            self.synced = True
            self.last_sync = timestamp
            self.last_sync_ms = now
            self.error = None
            self._finish(now, True)
            if self.event:
                self.event('network', 'Clock synchronized using NTP')
            return True
        except OSError as exc:
            if _code(exc) not in AGAIN:
                self.error = str(exc)[:120]
                self._finish(now)
        return False
