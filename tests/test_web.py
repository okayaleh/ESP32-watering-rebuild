import json
import os
import sys
import tempfile
import threading
import time
import http.client
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import web


class FakeSocket:
    def __init__(self, request=b'', read_size=512, send_size=512):
        self.request = bytearray(request)
        self.output = bytearray()
        self.read_size = read_size
        self.send_size = send_size
        self.closed = False
        self.blocking = None
        self.send_calls = []
        self.block_send = False

    def setblocking(self, value):
        self.blocking = value

    def recv(self, count):
        if not self.request:
            raise OSError(11)
        count = min(count, self.read_size, len(self.request))
        result = bytes(self.request[:count])
        del self.request[:count]
        return result

    def send(self, data):
        self.send_calls.append(len(data))
        if self.block_send:
            raise OSError(11)
        count = min(self.send_size, len(data))
        self.output.extend(data[:count])
        return count

    def close(self):
        self.closed = True


class FakeListener:
    def __init__(self, clients):
        self.clients = list(clients)
        self.error = 11
        self.closed = False

    def accept(self):
        if self.clients:
            return self.clients.pop(0), ('127.0.0.1', 1)
        raise OSError(self.error)

    def close(self):
        self.closed = True


class Sink:
    def __init__(self, name):
        self.name = name
        self.content = bytearray()
        self.aborted = False
        self.finished = False

    def write(self, data):
        if len(data) > 512:
            raise AssertionError('Oversized flash write')
        self.content.extend(data)

    def finish(self):
        self.finished = True
        return {'name': self.name, 'size': len(self.content)}

    def abort(self):
        self.aborted = True


def request(path='/api/status', body=None, content_type='application/json'):
    payload = b'' if body is None else body
    return (('%s %s HTTP/1.1\r\nHost: planter\r\nContent-Length: %d\r\nContent-Type: %s\r\n\r\n' %
             ('GET' if body is None else 'POST', path, len(payload), content_type)).encode() + payload)


class WebTests(unittest.TestCase):
    def test_json_strings_escape_in_bounded_fragments_with_short_fast_path(self):
        values = ['', 'plain', '"\\\n\t\r\b\f\x00\x1f\x7f',
                  'Jardín 🌱', '🌱' * 32, '\u0080' * 32,
                  '🌱' * 33, ('quoted "bed"\\\n🌱' * 40)]
        dumps = json.dumps
        for value in values:
            with self.subTest(length=len(value)):
                calls = []
                def bounded_dump(part):
                    self.assertIsInstance(part, str)
                    self.assertLessEqual(len(part), 32)
                    calls.append(part)
                    return dumps(part)
                with patch.object(web.json, 'dumps', bounded_dump):
                    fragments = list(web._json_string(value))
                encoded = b''.join(fragments)
                self.assertEqual(json.loads(encoded), value)
                self.assertEqual(encoded, dumps(value).encode('utf-8'))
                self.assertLessEqual(max(map(len, fragments)), 512)
                if len(value) <= 32:
                    self.assertEqual(calls, [value])
                    self.assertEqual(len(fragments), 1)
                else:
                    self.assertGreater(len(fragments), 1)
        self.assertEqual(len(next(web._json_string('🌱' * 32))), 386)

    def test_aborted_esp32_connection_keeps_listener_available(self):
        server = web.HTTPServer(lambda *args: {'ok': True})
        listener = FakeListener([])
        listener.error = 113  # ESP32/newlib ECONNABORTED, unlike Linux's 103.
        server.listener = listener
        server.state = 'listening'
        server.poll(0)
        self.assertIs(server.listener, listener)
        self.assertFalse(listener.closed)
        self.assertTrue(server.health()['listening'])
    def test_requests_and_uploads_with_micropython_bytearray_operations(self):
        class DeviceBytearray(bytearray):
            def __delitem__(self, key):
                raise TypeError('bytearray does not support item deletion')
        # Exercise real request parsing and every multipart boundary split with
        # the device's bytearray deletion limitation, absent from CPython.
        with patch.object(web, 'bytearray', DeviceBytearray, create=True):
            self.test_partial_writes_and_unicode_do_not_truncate()
            self.test_upload_every_boundary_split_and_binary_boundary_lookalike()

    def test_low_heap_sheds_work_but_preserves_stop_request(self):
        called = []
        handler = lambda *args: called.append(args[1]) or {'ok': True}
        client = FakeSocket(request())
        server = self.server(client, handler, load_shed=lambda: True)
        self.finish(server, client)
        self.assertIn(b'503 Service Unavailable', client.output)
        self.assertEqual(called, [])
        stop = FakeSocket(request('/api/water/stop', b''))
        server = self.server(stop, handler, load_shed=lambda: True)
        self.finish(server, stop)
        self.assertIn(b'200 OK', stop.output)
        self.assertEqual(called, ['/api/water/stop'])
    def server(self, client, handler=None, **kwargs):
        server = web.HTTPServer(handler or (lambda *args: {'ok': True}), **kwargs)
        server.listener = FakeListener([client])
        server.state = 'listening'
        return server

    def finish(self, server, client, limit=2000):
        for i in range(limit):
            server.poll(i * 10)
            if client.closed:
                return
        self.fail('Request did not finish incrementally')

    def body(self, client):
        header, body = bytes(client.output).split(b'\r\n\r\n', 1)
        return header, json.loads(body)

    def test_partial_writes_and_unicode_do_not_truncate(self):
        client = FakeSocket(request(), read_size=3, send_size=7)
        data = {'zones': [{'name': 'Jardín 🌱', 'moisture': i} for i in range(8)]}
        server = self.server(client, lambda *args: data)
        self.finish(server, client)
        header, body = self.body(client)
        self.assertEqual(body, data)
        self.assertIn(b'Connection: close', header)
        self.assertTrue(all(n <= 512 for n in client.send_calls))
        self.assertFalse(client.blocking)

    def test_largest_configuration_streams_without_dumping_containers(self):
        data = {'boards': [72, 73, 74, 75],
                'zones': [{'name': 'zone%d' % i, 'valves': ['valve%d' % j for j in range(8)]} for i in range(8)],
                'valves': [{'name': 'valve%d' % i, 'pin': i + 20} for i in range(8)],
                'schedules': [{'id': i, 'hour': 6, 'valve_names': ['valve%d' % j for j in range(8)]} for i in range(20)]}
        dumps = json.dumps
        calls = []
        def bounded_dump(value):
            self.assertIsInstance(value, str)
            self.assertLessEqual(len(value), 48)
            calls.append(value)
            return dumps(value)
        client = FakeSocket(request())
        server = self.server(client, lambda *args: data)
        with patch.object(web.json, 'dumps', bounded_dump):
            self.finish(server, client)
        self.assertEqual(self.body(client)[1], data)
        self.assertEqual(bytes(client.output), b''.join(web.response_fragments(web.Response(data))))
        self.assertGreater(len(calls), 100)
        self.assertLessEqual(max(client.send_calls), 512)

    def test_two_clients_bound_encoder_work_and_sends_per_poll(self):
        generated = [0, 0]
        def fragments(number):
            for unused in range(100):
                generated[number] += 1
                yield b'x'
        clients = [FakeSocket(), FakeSocket()]
        server = web.HTTPServer(lambda *args: None)
        for number, sock in enumerate(clients):
            client = web._Client(sock, 0)
            client.output = fragments(number)
            server.clients.append(client)
        server.poll(1)
        self.assertEqual(generated, [8, 8])
        self.assertEqual([len(sock.send_calls) for sock in clients], [1, 1])
        self.assertEqual([bytes(sock.output) for sock in clients], [b'x' * 8] * 2)
        clients[0].block_send = True
        server.poll(2)
        self.assertEqual(generated, [16, 16])
        self.assertEqual(bytes(clients[1].output), b'x' * 16)
        # Pending output is retained without generating another slice for a
        # blocked peer, while the other client continues making progress.
        server.poll(3)
        self.assertEqual(generated, [16, 24])
        self.assertEqual(bytes(clients[1].output), b'x' * 24)
        server.close()

    def test_lazy_history_is_not_materialized_and_closes_on_disconnect(self):
        seen = []
        closed = []
        def history():
            try:
                for index in range(10000):
                    seen.append(index)
                    yield {'at': index, 'moisture': [20] * 8}
            finally:
                closed.append(True)
        client = FakeSocket(request('/api/history'))
        server = self.server(client, lambda *args: web.JsonArray(history()))
        server.poll(0)
        server.poll(1)
        self.assertGreater(len(seen), 0)
        self.assertLess(len(seen), 100)
        server.close()
        self.assertEqual(closed, [True])

    def test_bounded_history_two_clients_partial_writes_and_unicode(self):
        names = ['Jardín 🌱 "%02d"\\' % index for index in range(16)]
        expected = [{'t': 1720000000 + index * 60,
                     'readings': [{'name': name, 'percent': None if zone == 0 else zone / 2}
                                  for zone, name in enumerate(names)]}
                    for index in range(180)]
        seen, closed = [0, 0], []
        def history(number):
            try:
                for point in expected:
                    seen[number] += 1
                    yield point
            finally:
                closed.append(number)
        class IntermittentSocket(FakeSocket):
            def __init__(self, send_size):
                super().__init__(request('/api/history'), send_size=send_size)
                self.attempts = 0
            def send(self, data):
                self.attempts += 1
                if self.attempts % 7 == 0:
                    raise OSError(11)
                return super().send(data)
        clients = [IntermittentSocket(113), IntermittentSocket(157)]
        issued = []
        def handler(*args):
            number = len(issued)
            issued.append(number)
            return web.JsonArray(history(number), skip_none=True, history_records=True)
        server = self.server(clients[0], handler)
        server.listener.clients.append(clients[1])
        dumps, native_records = json.dumps, []
        def bounded_dump(value):
            self.assertIs(type(value), dict)
            self.assertLessEqual(len(value['readings']), 16)
            encoded = dumps(value)
            self.assertLessEqual(len(encoded.encode()), 4096)
            native_records.append(value['t'])
            return encoded
        with patch.object(web.json, 'dumps', bounded_dump):
            server.poll(0)
            server.poll(1)
            self.assertLess(max(seen), 10, 'History was materialized before transmission')
            for now in range(2, 10000):
                server.poll(now)
                if all(client.closed for client in clients):
                    break
            else:
                self.fail('Partial-write history clients did not finish')
        self.assertEqual(seen, [180, 180])
        self.assertEqual(sorted(closed), [0, 1])
        self.assertEqual(len(native_records), 360)
        self.assertEqual(server.completed, 2)
        self.assertEqual(server.failed, 0)
        for client in clients:
            self.assertEqual(self.body(client)[1], expected)
            self.assertLessEqual(max(client.send_calls), 512)

    def test_bounded_history_legacy_fallback_and_nonfinite_values(self):
        def point(name='bed', percent=50, **extra):
            value = {'t': 1720000000, 'readings': [{'name': name, 'percent': percent}]}
            value.update(extra)
            return value
        rows = [point(name='x' * 49), point(name='🌱' * 13), point(name='line\nbreak'),
                point(percent=float('nan')), point(percent=float('inf')),
                point(percent=-1), point(percent=101), point(percent=True),
                point(extra={'old_field': 'preserved'}), point(t=10 ** 80),
                point(readings=[{'name': 'bed', 'percent': 50}] * 17),
                point(readings=[{'name': 'bed', 'percent': 50, 'raw': 123}]),
                point(name='\x7f' * 48)]
        dumps = json.dumps
        def scalar_only(value):
            self.assertIsInstance(value, str, 'Unbounded legacy container was dumped')
            return dumps(value)
        with patch.object(web.json, 'dumps', scalar_only):
            chunks = list(web.json_fragments(web.JsonArray(iter(rows), history_records=True)))
        expected = rows[:]
        expected[3] = point(percent=None)
        expected[4] = point(percent=None)
        self.assertEqual(json.loads(b''.join(chunks)), expected)
        self.assertLessEqual(max(map(len, chunks)), 512)

    def test_bounded_history_worst_name_escaping_and_fragment_size(self):
        for name in ('"\\' * 24, '\u0080' * 24, '🌱' * 12):
            row = {'t': -9999999999,
                   'readings': [{'name': name, 'percent': 0.12345678901234567}] * 16}
            self.assertTrue(web._small_history_record(row))
            chunks = list(web.json_fragments(web.JsonArray(iter([row]), history_records=True)))
            self.assertLessEqual(max(map(len, chunks)), 512)
            self.assertEqual(json.loads(b''.join(chunks)), [row])
            self.assertLessEqual(len(json.dumps(row).encode()), 4096)

    def test_bounded_history_checkpoints_and_disconnect_close_iterator(self):
        examined, closed = [], []
        def history():
            try:
                for index in range(5):
                    examined.append(None)
                    yield None
                for index in range(10000):
                    examined.append(index)
                    yield {'t': index, 'readings': [{'name': 'bed', 'percent': 50}]}
            finally:
                closed.append(True)
        client = FakeSocket(request('/api/history'))
        server = self.server(client, lambda *args: web.JsonArray(history(), skip_none=True,
                                                                 history_records=True))
        server.poll(0)
        for now in range(1, 6):
            server.poll(now)
            self.assertEqual(examined, [None] * now)
        server.poll(6)
        self.assertGreater(len(examined), 5)
        self.assertLess(len(examined), 100)
        server.close()
        self.assertEqual(closed, [True])

    def test_history_name_cache_reuses_validation_and_utf8_size_per_request(self):
        names = ['Jardín 🌱 %02d' % index for index in range(16)]
        point = {'t': 1720000000,
                 'readings': [{'name': name, 'percent': 50} for name in names]}
        first = web.JsonArray(iter([point] * 180), history_records=True)
        second = web.JsonArray(iter([point]), history_records=True)
        with patch.object(web, 'ord', wraps=ord, create=True) as inspected:
            self.assertEqual(len(json.loads(b''.join(web.json_fragments(first)))), 180)
            self.assertEqual(inspected.call_count, sum(map(len, names)))
            list(web.json_fragments(second))
            self.assertEqual(inspected.call_count, 2 * sum(map(len, names)))
        self.assertIsNot(first._history_names, second._history_names)
        self.assertEqual(first._history_names, {name: len(name.encode()) for name in names})
        wide = {'t': 1, 'readings': [{'name': '\u0080' * 24, 'percent': 50}]}
        cache = {}
        self.assertTrue(web._small_history_record(wide, cache))
        self.assertEqual(cache[wide['readings'][0]['name']], 48)
        # Cached UTF-8 bytes, not character count, must still drive the bound.
        with patch.object(web, 'MAX_HISTORY_RECORD', 250):
            self.assertFalse(web._small_history_record(wide, cache))

    def test_history_name_cache_caps_retention_and_rechecks_invalid_records(self):
        def point(name, percent=50):
            return {'t': 1720000000, 'readings': [{'name': name, 'percent': percent}]}
        names = ['bed%02d' % index for index in range(80)]
        invalid = ['', '\x7f' * 48, 'line\nbreak', '🌱' * 13, 'x' * 49]
        rows = [point(name) for name in names]
        rows += [point(name) for unused in range(3) for name in invalid]
        rows += [point(names[0], float('nan')), point(names[1], 101)]
        rows.append({'t': True, 'readings': [{'name': names[0], 'percent': 50}]})
        rows.append({'t': 1, 'readings': [{'name': names[0], 'percent': 50, 'old': 1}]})
        array = web.JsonArray(iter(rows), history_records=True)
        dumps, native_rows = json.dumps, []
        def observe(value):
            if type(value) is dict:
                native_rows.append(value)
            return dumps(value)
        with patch.object(web.json, 'dumps', observe):
            decoded = json.loads(b''.join(web.json_fragments(array)))
        self.assertEqual(native_rows, rows[:80])
        self.assertEqual(len(array._history_names), 32)
        self.assertEqual(set(array._history_names), set(names[:32]))
        self.assertTrue(all(name not in array._history_names for name in invalid))
        expected = rows[:]
        expected[-4] = point(names[0], None)
        self.assertEqual(decoded, expected)

    def test_history_excluded_rows_yield_control_once_per_poll(self):
        examined = []
        def history():
            for index in range(10000):
                examined.append(index)
                yield None
            yield {'t': 1, 'readings': []}
        client = FakeSocket(request('/api/history?hours=1'))
        server = self.server(client, lambda *args: web.JsonArray(history(), skip_none=True))
        server.poll(0)
        self.assertEqual(examined, [])
        for index in range(1, 11):
            server.poll(index)
            self.assertEqual(len(examined), index)
        self.assertFalse(client.closed)
        server.close()
        self.assertEqual(b''.join(web.json_fragments(web.JsonArray(iter([None, {'ok': True}, None]), skip_none=True))), b'[{"ok":true}]')
        self.assertEqual(b''.join(web.json_fragments(web.JsonArray(iter([None])))), b'[null]')

    def test_two_client_limit_and_slow_peer_does_not_block_other(self):
        slow = FakeSocket(request())
        slow.block_send = True
        fast = FakeSocket(request())
        extra = FakeSocket(request())
        server = self.server(slow)
        server.listener.clients.extend([fast, extra])
        for now in range(10):
            server.poll(now)
            self.assertLessEqual(len(server.clients), 2)
        self.assertTrue(fast.closed)
        self.assertTrue(extra.closed)
        self.assertFalse(slow.closed)
        server.poll(10001)
        self.assertTrue(slow.closed)

    def test_listener_eagain_is_idle_but_fatal_error_marks_fault(self):
        server = self.server(FakeSocket())
        server.listener.clients = []
        server.poll(0)
        self.assertEqual(server.health()['state'], 'listening')
        server.listener.error = 9
        server.poll(1)
        self.assertIsNone(server.listener)
        self.assertEqual(server.health()['state'], 'fault')
        self.assertFalse(server.health()['listening'])
        self.assertEqual(server.health()['reachability'], 'unverified')
        with patch.object(server, 'start', return_value=True) as start:
            server.poll(30000)
            start.assert_not_called()
            server.poll(30001)
            start.assert_called_once()

    def test_requests_reject_untrusted_lengths_and_duplicate_headers(self):
        for value in ('-1', 'abc', '16385', '999999999999999999999'):
            client = FakeSocket(('POST /api/settings HTTP/1.1\r\nContent-Length: %s\r\n\r\n' % value).encode())
            server = self.server(client)
            self.finish(server, client)
            self.assertIn(self.body(client)[0].split()[1], (b'400', b'413'))
        client = FakeSocket(b'POST /api/settings HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n')
        server = self.server(client)
        self.finish(server, client)
        self.assertIn(b'400', self.body(client)[0])

    def test_form_setup_and_captive_probe(self):
        calls = []
        client = FakeSocket(request('/api/wifi', b'ssid=Jard%C3%ADn&password=a%26b', 'application/x-www-form-urlencoded'))
        server = self.server(client, lambda *args: calls.append(args) or {'ok': True}, redirect=lambda: True)
        self.finish(server, client)
        self.assertEqual(calls[0][3], {'ssid': 'Jardín', 'password': 'a&b'})
        client = FakeSocket(request('/generate_204'))
        server = self.server(client, redirect=lambda: True)
        self.finish(server, client)
        self.assertIn(b'302 Found', client.output)
        self.assertIn(b'Location: http://192.168.4.1/setup', client.output)

    def test_upload_every_boundary_split_and_binary_boundary_lookalike(self):
        payload = b'abc\x00\r\n--gardenX!data' * 50
        body = b'--garden\r\nContent-Disposition: form-data; name="file"; filename="app.mpy"\r\n\r\n' + payload + b'\r\n--garden--\r\n'
        for read_size in (1, 2, 7, 69, 512):
            sinks = []
            def factory(name):
                sink = Sink(name)
                sinks.append(sink)
                return sink
            client = FakeSocket(request('/api/upload', body, 'multipart/form-data; boundary=garden'), read_size=read_size)
            server = self.server(client, upload_factory=factory)
            self.finish(server, client, 4000)
            self.assertTrue(self.body(client)[1]['ok'])
            self.assertEqual(bytes(sinks[0].content), payload)
            self.assertTrue(sinks[0].finished)

    def test_upload_deadline_aborts_trickling_upload(self):
        sink = Sink('runtime.mpy')
        body = b'--garden\r\nContent-Disposition: form-data; name="file"; filename="runtime.mpy"\r\n\r\n' + b'x' * 10000
        client = FakeSocket(request('/api/upload', body, 'multipart/form-data; boundary=garden'), read_size=512)
        server = self.server(client, upload_factory=lambda name: sink)
        server.poll(0)
        self.assertEqual(server.health()['active_uploads'], 1)
        for now in range(9000, 120000, 9000):
            server.poll(now)
        server.poll(120000)
        self.assertTrue(client.closed)
        self.assertTrue(sink.aborted)
        self.assertFalse(sink.finished)
        self.assertEqual(server.health()['active_uploads'], 0)

    def test_upload_traversal_and_size_rejected_before_factory(self):
        calls = []
        for filename in ('../boot.py', 'sub\\main.py'):
            body = ('--b\r\nContent-Disposition: form-data; filename="%s"\r\n\r\nx\r\n--b--\r\n' % filename).encode()
            client = FakeSocket(request('/api/upload', body, 'multipart/form-data; boundary=b'))
            server = self.server(client, upload_factory=lambda name: calls.append(name))
            self.finish(server, client)
            self.assertIn(b'400', self.body(client)[0])
        self.assertEqual(calls, [])

    def test_gzip_dashboard_streams_from_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'index.html.gz')
            with open(path, 'wb') as stream:
                stream.write(b'compressed' * 300)
            client = FakeSocket(b'GET / HTTP/1.1\r\nAccept-Encoding: gzip\r\n\r\n')
            server = self.server(client, root=directory)
            self.finish(server, client)
            head, data = bytes(client.output).split(b'\r\n\r\n', 1)
            self.assertIn(b'Content-Encoding: gzip', head)
            self.assertEqual(data, b'compressed' * 300)

    def test_real_sockets_two_clients_repeat_max_configuration_unicode(self):
        expected = {'zones': [{'name': 'Jardín 🌱 %d' % i, 'channels': list(range(16))}
                              for i in range(8)],
                    'valves': [{'name': 'valve%d' % i, 'open': False} for i in range(8)],
                    'schedules': [{'id': i, 'hour': 6, 'valve_names': ['valve%d' % j for j in range(8)]}
                                  for i in range(20)]}
        server = web.HTTPServer(lambda *args: expected, port=0)
        self.assertTrue(server.start())
        port = server.listener.getsockname()[1]
        stop = threading.Event()
        failures = []
        samples = []
        def run_server():
            try:
                while not stop.is_set():
                    started = time.monotonic()
                    server.poll()
                    samples.append(time.monotonic() - started)
                    time.sleep(0.001)
            except Exception as exc:
                failures.append(exc)
        def browser():
            try:
                for index in range(40):
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                    try:
                        connection.request('GET', '/api/status')
                        response = connection.getresponse()
                        if response.status != 200 or json.loads(response.read()) != expected:
                            raise AssertionError('Incomplete maximum-configuration response')
                    finally:
                        connection.close()
            except Exception as exc:
                failures.append(exc)
        runner = threading.Thread(target=run_server, daemon=True)
        browsers = [threading.Thread(target=browser, daemon=True) for unused in range(2)]
        try:
            runner.start()
            for thread in browsers:
                thread.start()
            for thread in browsers:
                thread.join(30)
            self.assertTrue(all(not thread.is_alive() for thread in browsers), 'Socket stress deadline exceeded')
        finally:
            stop.set()
            runner.join(2)
            server.close()
        self.assertEqual(failures, [])
        self.assertEqual(server.completed, 80)
        self.assertGreater(len(samples), 80)
        # A loose desktop threshold catches accidental waits without depending
        # on submillisecond scheduling; device latency needs hardware testing.
        self.assertLess(max(samples), 1.0)


if __name__ == '__main__':
    unittest.main()
