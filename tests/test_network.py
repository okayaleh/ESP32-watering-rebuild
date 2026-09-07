import importlib
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class Radio:
    def __init__(self):
        self.calls = []
        self.linked = False
        self.enabled = False
        self.code = 1

    def active(self, value=None):
        if value is not None:
            self.calls.append(('active', value))
            self.enabled = value
        return self.enabled

    def config(self, **kwargs):
        if not self.enabled:
            raise OSError('Wifi Invalid Mode')
        self.calls.append(('config', kwargs))

    def connect(self, ssid, password):
        self.calls.append(('connect', ssid, password))

    def disconnect(self):
        self.calls.append(('disconnect',))
        self.linked = False

    def isconnected(self):
        return self.linked

    def status(self, key=None):
        return -45 if key == 'rssi' else self.code

    def ifconfig(self, value=None):
        if value:
            self.calls.append(('ifconfig', value))
        return ('192.168.1.5', '255.255.255.0', '192.168.1.1', '192.168.1.1')


class WLANFactory:
    PM_NONE = 0
    def __init__(self):
        self.station = Radio()
        self.ap = Radio()
    def __call__(self, interface):
        return self.station if interface == 0 else self.ap


def network_stub():
    return types.SimpleNamespace(WLAN=WLANFactory(), STA_IF=0, AP_IF=1,
                                 AUTH_OPEN=0, STAT_WRONG_PASSWORD=-3,
                                 STAT_NO_AP_FOUND=-2, STAT_CONNECT_FAIL=-1)


with patch.dict(sys.modules, {'network': network_stub()}):
    import wifi
    import wifi_setup


class Datagram:
    def __init__(self):
        self.incoming = []
        self.sent = []
        self.blocking = None
        self.closed = False
    def setblocking(self, value):
        self.blocking = value
    def bind(self, address):
        self.bound = address
    def sendto(self, data, address):
        self.sent.append((bytes(data), address))
        return len(data)
    def recvfrom(self, size):
        if not self.incoming:
            raise OSError(11)
        return self.incoming.pop(0)
    def close(self):
        self.closed = True


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.net = network_stub()
        self.patch_network = patch.object(wifi, 'network', self.net)
        self.patch_portal_network = patch.object(wifi_setup, 'network', self.net)
        self.patch_network.start()
        self.patch_portal_network.start()
        self.addCleanup(self.patch_network.stop)
        self.addCleanup(self.patch_portal_network.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = types.SimpleNamespace(WIFI_SSID='garden', WIFI_PASSWORD='secret',
                                            WIFI_RESCUE_AFTER_SEC=300)

    def manager(self):
        with patch.object(wifi, 'ticks_ms', return_value=0):
            return wifi.WifiManager(self.config, os.path.join(self.directory.name, 'wifi.json'))

    def test_nonblocking_connect_and_exponential_retry(self):
        manager = self.manager()
        manager.poll(0)
        self.assertEqual(manager.state, 'connecting')
        self.assertIn(('config', {'pm': 0}), self.net.WLAN.station.calls)
        self.net.WLAN.station.code = -2
        manager.poll(1)
        self.assertEqual(manager.state, 'connecting')
        manager.poll(20000)
        self.assertEqual(manager.state, 'offline')
        self.assertFalse(manager.portal.active)
        self.assertEqual(manager.next_attempt, 21000)
        manager.poll(21000)
        manager.poll(41000)
        self.assertEqual(manager.next_attempt, 43000)
        self.assertEqual(manager.backoff_ms, 4000)

    def test_stale_native_failure_survives_retry_until_link_success(self):
        self.net.STAT_NO_AP_FOUND = 201
        for with_portal in (False, True):
            with self.subTest(with_portal=with_portal):
                manager = self.manager()
                manager.wlan.linked = False
                manager.wlan.code = 201
                with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()):
                    if with_portal:
                        manager._rescue(0)
                    manager.poll(manager.next_attempt)
                    # Complete one failed attempt, leaving the driver's old code.
                    manager.poll(manager.connect_deadline)
                    self.assertEqual(manager.failures, 1)
                    self.assertEqual(manager.last_error, 'WiFi network not found (status 201)')
                    retry = manager.next_attempt
                    manager.poll(retry)
                    budget = 12000 if with_portal else 20000
                    self.assertEqual(manager.connect_deadline, retry + budget)
                    for elapsed in (1, 1000, budget - 2):
                        manager.poll(retry + elapsed)
                        self.assertEqual(manager.state, 'connecting')
                        self.assertEqual(manager.failures, 1)
                        self.assertTrue(manager.wlan.enabled)
                    # The retained error must not prevent DHCP/GOT_IP later.
                    manager.wlan.linked = True
                    manager.poll(retry + budget - 1)
                self.assertEqual(manager.state, 'connected')
                self.assertTrue(manager.connected)
                self.assertEqual(manager.failures, 0)
                self.assertIsNone(manager.last_error)
                self.assertFalse(manager.portal.active)

    def test_connect_deadline_uses_latest_native_reason_to_offer_rescue(self):
        self.net.STAT_WRONG_PASSWORD = 202
        self.net.STAT_NO_AP_FOUND = 201
        manager = self.manager()
        manager.wlan.code = 201
        manager.poll(0)
        manager.poll(19999)
        self.assertEqual(manager.state, 'connecting')
        self.assertEqual(manager.failures, 0)
        self.assertFalse(manager.portal.active)
        manager.wlan.code = 202
        with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()), \
                patch.object(manager, '_failed', wraps=manager._failed) as failed:
            manager.poll(20000)
            failed.assert_called_once_with(20000, 202)
        self.assertEqual(manager.state, 'rescue')
        self.assertTrue(manager.portal.active)
        self.assertEqual(manager.status()['last_error'], 'WiFi authentication failed (status 202)')
        self.assertFalse(manager.wlan.enabled)
        self.assertEqual(manager.next_attempt, 80000)
        # Rescue still gives its next attempt the entire 12-second budget.
        manager.poll(80000)
        manager.poll(91999)
        self.assertEqual(manager.state, 'connecting')
        self.assertEqual(manager.failures, 1)
        manager.poll(92000)
        self.assertEqual(manager.state, 'rescue')
        self.assertEqual(manager.failures, 2)
        self.assertEqual(manager.next_attempt, 152000)

    def test_deadline_reports_bounded_native_failure_reason(self):
        self.net.STAT_NO_AP_FOUND = 201
        self.net.STAT_CONNECT_FAIL = 203
        for code, reason in ((201, 'WiFi network not found'),
                             (203, 'WiFi connection failed'),
                             (1001, 'WiFi connection timed out')):
            with self.subTest(code=code):
                manager = self.manager()
                manager.wlan.code = code
                manager.poll(0)
                manager.poll(20000)
                error = manager.status()['last_error']
                self.assertEqual(error, '%s (status %s)' % (reason, code))
                self.assertLessEqual(len(error), 120)
                self.assertNotIn(self.config.WIFI_PASSWORD, error)
                self.assertNotIn(self.config.WIFI_SSID, error)

    def test_native_connect_runtime_error_uses_backoff_and_remains_observable(self):
        manager = self.manager()
        events = []
        manager.event = lambda kind, message: events.append((kind, message))
        with patch.object(manager.wlan, 'connect', side_effect=RuntimeError('Wifi Unknown Error 0xffffffff')) as connect:
            manager.poll(0)
            self.assertEqual(manager.state, 'offline')
            self.assertEqual(manager.failures, 1)
            self.assertEqual(manager.next_attempt, 1000)
            self.assertIn('0xffffffff', manager.last_error)
            manager.poll(1)
            manager.poll(999)
            self.assertEqual(connect.call_count, 1)
            manager.poll(1000)
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(manager.next_attempt, 3000)
            self.assertFalse(manager.wlan.enabled)
        self.assertTrue(any('connection failed' in message for kind, message in events))

    def test_optional_driver_runtime_errors_do_not_prevent_connection(self):
        manager = self.manager()
        events = []
        manager.event = lambda kind, message: events.append(message)
        with patch.object(manager.wlan, 'config', side_effect=RuntimeError('Wifi Unknown Error 0xffffffff')):
            manager.poll(0)
        self.assertEqual(manager.state, 'connecting')
        self.assertIn(('connect', 'garden', 'secret'), manager.wlan.calls)
        self.assertTrue(any('retry configuration unavailable' in message for message in events))
        self.assertTrue(any('power-save configuration unavailable' in message for message in events))

    def test_native_poll_runtime_error_is_reported_without_escaping(self):
        manager = self.manager()
        events = []
        manager.event = lambda kind, message: events.append(message)
        with patch.object(manager.wlan, 'isconnected', side_effect=RuntimeError('Wifi Unknown Error 0xffffffff')):
            manager.poll(0)
        self.assertIn('0xffffffff', manager.last_error)
        self.assertTrue(any('WiFi poll error:' in message for message in events))

    def test_native_status_runtime_error_returns_safe_telemetry_defaults(self):
        manager = self.manager()
        manager.connected = True
        for field in ('ifconfig', 'status'):
            with self.subTest(field=field), patch.object(manager.wlan, field,
                    side_effect=RuntimeError('Wifi Unknown Error 0xffffffff')):
                result = manager.status()
                self.assertIsNone(result['ip'])
                self.assertIsNone(result['rssi'])
                self.assertIn('0xffffffff', result['last_error'])
                self.assertNotIn('password', result)

    def test_setup_ap_order_and_station_parked_between_retries(self):
        self.config.WIFI_SSID = ''
        manager = self.manager()
        with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()):
            manager.poll(0)
        calls = self.net.WLAN.ap.calls
        config_index = next(i for i, call in enumerate(calls) if call[0] == 'config')
        self.assertEqual(calls[config_index - 1], ('active', True))
        self.assertEqual(calls[config_index + 1:config_index + 3], [('active', False), ('active', True)])
        self.assertEqual(calls[config_index][1]['password'], '')
        self.assertFalse(self.net.WLAN.station.enabled)
        self.assertTrue(manager.portal.active)

    def test_blank_setup_never_disconnects_inactive_sta_or_retries_it_each_minute(self):
        self.config.WIFI_SSID = ''
        manager = self.manager()
        station = self.net.WLAN.station
        dns = Datagram()
        with patch.object(station, 'disconnect', side_effect=RuntimeError('Wifi Unknown Error 0xffffffff')) as disconnect, \
                patch.object(station, 'isconnected', side_effect=AssertionError('No STA polling before provisioning')), \
                patch.object(wifi_setup.socket, 'socket', return_value=dns):
            manager.poll(0)
            self.assertTrue(manager.portal.active)
            ap_calls = self.net.WLAN.ap.calls[:]
            sta_calls = station.calls[:]
            query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x05apple\x03com\x00\x00\x01\x00\x01'
            for now in (59999, 60000, 120000, 300000):
                dns.incoming.append((query, ('192.168.4.2', 50000)))
                manager.poll(now)
            disconnect.assert_not_called()
            self.assertEqual(station.calls, sta_calls)
            self.assertEqual(self.net.WLAN.ap.calls, ap_calls)
            self.assertEqual(len(dns.sent), 4)
            self.assertTrue(all(packet[-4:] == b'\xc0\xa8\x04\x01' for packet, address in dns.sent))
            self.assertEqual(manager.state, 'rescue')
            self.assertIsNone(manager.last_error)

    def test_blank_setup_leaves_temporary_scan_station_active(self):
        self.config.WIFI_SSID = ''
        manager = self.manager()
        with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()):
            manager.poll(0)
        station = self.net.WLAN.station
        station.active(True)  # Setup code may temporarily enable STA for scan.
        calls = station.calls[:]
        for now in (60000, 120000):
            manager.poll(now)
        self.assertTrue(station.enabled)
        self.assertEqual(station.calls, calls)
        self.assertTrue(manager.portal.active)

    def test_blank_setup_failure_backs_off_and_recovers(self):
        self.config.WIFI_SSID = ''
        manager = self.manager()
        attempts = []
        def start():
            attempts.append(1)
            manager.portal.active = len(attempts) > 1
            return manager.portal.active
        with patch.object(manager.portal, 'start', side_effect=start):
            manager.poll(0)
            self.assertFalse(manager.portal.active)
            manager.poll(1)
            manager.poll(59999)
            self.assertEqual(len(attempts), 1)
            manager.poll(60000)
            self.assertTrue(manager.portal.active)
            self.assertEqual(len(attempts), 2)

    def test_station_parking_deactivates_even_after_native_disconnect_runtime_error(self):
        manager = self.manager()
        station = self.net.WLAN.station
        station.active(True)
        manager.state = 'connecting'
        with patch.object(station, 'disconnect', side_effect=RuntimeError('Wifi Unknown Error 0xffffffff')) as disconnect:
            manager._park()
        disconnect.assert_called_once()
        self.assertFalse(station.enabled)
        self.assertEqual(station.calls[-1], ('active', False))

    def test_station_parking_skips_disconnect_for_unassociated_scan_interface(self):
        manager = self.manager()
        station = self.net.WLAN.station
        station.active(True)
        with patch.object(station, 'disconnect', side_effect=AssertionError('No association to disconnect')):
            manager._park()
        self.assertFalse(station.enabled)

    def test_blank_setup_saved_credentials_connect_on_next_start(self):
        self.config.WIFI_SSID = ''
        manager = self.manager()
        with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()):
            manager.poll(0)
        manager.save_credentials('configured garden', 'new password')
        manager.portal.close()
        restarted = self.manager()
        restarted.poll(0)
        self.assertEqual(restarted.state, 'connecting')
        self.assertIn(('connect', 'configured garden', 'new password'), self.net.WLAN.station.calls)

    def test_rescue_closes_when_station_recovers(self):
        manager = self.manager()
        with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()):
            manager._rescue(0)
        self.net.WLAN.station.linked = True
        manager.poll(1)
        self.assertTrue(manager.connected)
        self.assertFalse(manager.portal.active)
        self.assertNotIn('password', manager.status())

    def test_saved_wrong_credentials_always_offer_setup(self):
        manager = self.manager()
        manager.save_credentials('other', 'password')
        manager = self.manager()
        manager.poll(0)
        self.net.WLAN.station.code = -2
        with patch.object(wifi_setup.socket, 'socket', return_value=Datagram()):
            manager.poll(20000)
        self.assertTrue(manager.portal.active)

    def test_dns_nonblocking_transaction_and_source_validation(self):
        sock = Datagram()
        with patch.object(wifi.socket, 'socket', return_value=sock):
            resolver = wifi.DNSResolver('192.168.1.1')
            self.assertIsNone(resolver.resolve('pool.ntp.org'))
            self.assertEqual(sock.sent, [])
            resolver.poll(0, allow_start=False)
            self.assertEqual(sock.sent, [])
            resolver.poll(1)
            query, target = sock.sent[0]
            self.assertFalse(sock.blocking)
            self.assertEqual(target, ('192.168.1.1', 53))
            answer = query[:2] + b'\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00' + query[12:] + b'\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04\x01\x02\x03\x04'
            sock.incoming.append((answer, ('8.8.8.8', 53)))
            resolver.poll(2)
            self.assertIsNone(resolver.resolve('pool.ntp.org'))
            sock.incoming.append((answer, ('192.168.1.1', 53)))
            resolver.poll(3)
            self.assertEqual(resolver.resolve('pool.ntp.org'), '1.2.3.4')
            self.assertTrue(sock.closed)

    def test_dns_timeout_negative_cache_and_numeric_address(self):
        with patch.object(wifi.socket, 'socket', return_value=Datagram()):
            resolver = wifi.DNSResolver('192.168.1.1')
            self.assertEqual(resolver.resolve('1.2.3.4'), '1.2.3.4')
            resolver.resolve('missing.example')
            resolver.poll(0)
            resolver.poll(3000)
            self.assertEqual(resolver.error, 'DNS timeout')
            self.assertIsNone(resolver.resolve('missing.example'))
            self.assertEqual(resolver.queue, [])

    def test_gateway_refusal_reset_and_unknown_errors(self):
        class ProbeSocket(Datagram):
            def connect(self, address):
                raise OSError(error)
        class Poller:
            def register(self, *args): pass
            def unregister(self, *args): pass
        for error, expected in ((111, 'healthy'), (104, 'healthy'), (110, 'timeout'), (12345, 'inconclusive')):
            constants = types.SimpleNamespace(poll=lambda: Poller(), POLLOUT=4, POLLERR=8, POLLHUP=16)
            with patch.object(wifi.socket, 'socket', return_value=ProbeSocket()), patch.object(wifi, 'select', constants):
                probe = wifi.GatewayProbe()
                self.assertEqual(probe.start('192.168.1.1', 0), expected)

    def test_gateway_completion_on_esp32_without_socket_introspection(self):
        class ProbeSocket(Datagram):
            def __init__(self, completion):
                super().__init__()
                self.completion = completion
                self.connect_calls = []
            def connect(self, address):
                self.connect_calls.append(address)
                if len(self.connect_calls) == 1:
                    raise OSError(115)
                if self.completion:
                    raise OSError(self.completion)
            def recv(self, size):
                if size != 1:
                    raise AssertionError('Probe read must be bounded to one byte')
                raise OSError(11)
        class Poller:
            def register(self, *args): pass
            def unregister(self, *args): pass
            def poll(self, timeout):
                self.timeout = timeout
                return [(1, 4)]
        for code, expected in ((0, 'healthy'), (106, 'healthy'), (127, 'healthy'), (10056, 'healthy'),
                               (111, 'healthy'), (104, 'healthy'), (110, 'timeout'),
                               (115, None), (119, None), (120, None), (12345, 'inconclusive')):
            sock, poller = ProbeSocket(code), Poller()
            sockets = types.SimpleNamespace(socket=lambda *args: sock, AF_INET=2, SOCK_STREAM=1)
            polls = types.SimpleNamespace(poll=lambda: poller, POLLOUT=4, POLLERR=8, POLLHUP=16)
            with patch.object(wifi, 'socket', sockets), patch.object(wifi, 'select', polls):
                probe = wifi.GatewayProbe()
                self.assertIsNone(probe.start('192.168.1.1', 0))
                self.assertEqual(probe.poll(1), expected)
                self.assertEqual(sock.connect_calls, [('192.168.1.1', 80)] * 2)
                self.assertFalse(sock.blocking)
                self.assertEqual(poller.timeout, 0)
                self.assertEqual(sock.closed, expected is not None)

    def test_gateway_reads_queued_lwip_error_before_reconnect_loses_it(self):
        class ProbeSocket(Datagram):
            def __init__(self):
                super().__init__()
                self.connect_calls = 0
                self.read_sizes = []
            def connect(self, address):
                self.connect_calls += 1
                # After async refusal, lwIP's deleted PCB loses the cause here.
                raise OSError(119 if self.connect_calls == 1 else 128)
            def recv(self, size):
                self.read_sizes.append(size)
                if isinstance(completion, int):
                    raise OSError(completion)
                return completion
        class Poller:
            def register(self, *args): pass
            def unregister(self, *args): pass
            def poll(self, timeout):
                if timeout != 0:
                    raise AssertionError('Probe poll must not block')
                return [(1, 20)]
        for completion, expected in ((104, 'healthy'), (111, 'healthy'),
                                     (b'', 'healthy'), (b'x', 'healthy'),
                                     (110, 'timeout'), (128, 'inconclusive'),
                                     (113, 'inconclusive'), (12345, 'inconclusive')):
            with self.subTest(completion=completion):
                sock = ProbeSocket()
                sockets = types.SimpleNamespace(socket=lambda *args: sock, AF_INET=2, SOCK_STREAM=1)
                polls = types.SimpleNamespace(poll=lambda: Poller(), POLLOUT=4, POLLERR=8, POLLHUP=16)
                with patch.object(wifi, 'socket', sockets), patch.object(wifi, 'select', polls):
                    probe = wifi.GatewayProbe()
                    self.assertIsNone(probe.start('192.168.1.1', 0))
                    self.assertEqual(probe.poll(1), expected)
                self.assertEqual(sock.connect_calls, 1)
                self.assertEqual(sock.read_sizes, [1])
                self.assertFalse(sock.blocking)
                self.assertTrue(sock.closed)

    def test_gateway_completion_with_so_error(self):
        class ProbeSocket(Datagram):
            def connect(self, address): raise OSError(115)
            def getsockopt(self, level, option): return completion
        class Poller:
            def register(self, *args): pass
            def unregister(self, *args): pass
            def poll(self, timeout): return [(1, 4)]
        for completion, expected in ((0, 'healthy'), (111, 'healthy'), (110, 'timeout')):
            sock = ProbeSocket()
            sockets = types.SimpleNamespace(socket=lambda *args: sock, AF_INET=2, SOCK_STREAM=1,
                                            SOL_SOCKET=1, SO_ERROR=4)
            polls = types.SimpleNamespace(poll=lambda: Poller(), POLLOUT=4, POLLERR=8, POLLHUP=16)
            with patch.object(wifi, 'socket', sockets), patch.object(wifi, 'select', polls):
                probe = wifi.GatewayProbe()
                self.assertIsNone(probe.start('192.168.1.1', 0))
                self.assertEqual(probe.poll(1), expected)

    def test_gateway_timeout_uses_wrap_safe_deadline(self):
        class ProbeSocket(Datagram):
            def connect(self, address): raise OSError(115)
        class Poller:
            def register(self, *args): pass
            def unregister(self, *args): pass
            def poll(self, timeout):
                self.timeout = timeout
                return []
        poller = Poller()
        constants = types.SimpleNamespace(poll=lambda: poller, POLLOUT=4, POLLERR=8, POLLHUP=16)
        with patch.object(wifi.socket, 'socket', return_value=ProbeSocket()), patch.object(wifi, 'select', constants):
            probe = wifi.GatewayProbe(3000)
            start = (1 << 30) - 1000
            self.assertIsNone(probe.start('192.168.1.1', start))
            self.assertIsNone(probe.poll(0))
            self.assertEqual(poller.timeout, 0)
            self.assertEqual(probe.poll(2000), 'timeout')

    def test_captive_dns_returns_ipv4_and_rejects_malformed_names(self):
        query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x05apple\x03com\x00\x00\x01\x00\x01'
        answer = wifi_setup.RescuePortal.dns_answer(query)
        self.assertEqual(answer[:2], b'\x12\x34')
        self.assertEqual(answer[-4:], b'\xc0\xa8\x04\x01')
        self.assertIsNone(wifi_setup.RescuePortal.dns_answer(query[:12] + b'\xff'))

    def test_ntp_checks_cookie_and_never_starts_during_watering(self):
        class Resolver:
            def __init__(self): self.calls = 0
            def resolve(self, host):
                self.calls += 1
                return '1.2.3.4'
        resolver = Resolver()
        sock = Datagram()
        stamps = []
        machine = types.SimpleNamespace(RTC=lambda: types.SimpleNamespace(datetime=lambda value: stamps.append(value)))
        with patch.object(wifi, 'ticks_ms', return_value=0):
            ntp = wifi.NTPClient(resolver=resolver)
        with patch.object(wifi.socket, 'socket', return_value=sock), patch.dict(sys.modules, {'machine': machine}):
            ntp.poll(0, connected=True, valve_open=True)
            self.assertEqual(resolver.calls, 0)
            ntp.poll(1, connected=True)
            self.assertEqual(len(sock.sent), 1)
            packet = bytearray(48)
            packet[0] = 0x24
            packet[1] = 2
            packet[40:44] = (int(time.time()) + 2208988800).to_bytes(4, 'big')
            sock.incoming.append((bytes(packet), ('1.2.3.4', 123)))
            self.assertFalse(ntp.poll(2, connected=True))
            self.assertEqual(stamps, [])
            packet[24:32] = ntp.cookie
            sock.incoming.append((bytes(packet), ('1.2.3.4', 123)))
            self.assertTrue(ntp.poll(3, connected=True))
            self.assertTrue(ntp.synced)
            self.assertEqual(len(stamps), 1)
            self.assertTrue(sock.closed)


if __name__ == '__main__':
    unittest.main()
