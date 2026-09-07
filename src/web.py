"""Cooperatively polled HTTP transport: no socket ever waits for a peer.

Two clients, 512-byte reads/writes, bounded framing and absolute deadlines
keep dashboard traffic subordinate to the controller's safety loop. The
underlying ESP32/lwIP listener fault still requires device soak testing.
"""
try:
    import usocket as socket
except ImportError:
    import socket
from compat import ticks_ms, ticks_diff, ticks_add, json

WOULD_BLOCK = (11, 35, 10035)
MAX_BODY = 16384
MAX_UPLOAD = 512 * 1024
MAX_HEADERS = 4096
CHUNK = 512
WRITES_PER_POLL = 1
FRAGMENTS_PER_WRITE = 8
MAX_HISTORY_RECORD = 4096
HISTORY_NAME_CACHE_LIMIT = 32
PROBES = ('/hotspot-detect.html', '/generate_204', '/gen_204', '/ncsi.txt', '/connecttest.txt')


def errno(exc):
    return exc.args[0] if exc.args else None


def unquote(value):
    data = value.replace('+', ' ').encode()
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 37:
            if i + 2 >= len(data):
                raise ValueError('Incomplete URL escape')
            result.append(int(data[i + 1:i + 3], 16))
            i += 3
        else:
            result.append(data[i])
            i += 1
    return result.decode('utf-8')


def parse_query(query):
    result = {}
    if query:
        for item in query.split('&'):
            pair = item.split('=', 1)
            result[unquote(pair[0])] = unquote(pair[1]) if len(pair) == 2 else ''
    return result


class JsonArray:
    def __init__(self, iterable, skip_none=False, history_records=False):
        self.iterable = iterable
        self.skip_none = skip_none
        self.history_records = history_records
        # Request-local, immutable name -> UTF-8 byte length. Retain at most
        # 32 names even when older history contains many renamed zones.
        self._history_names = {} if history_records else None


def _small_history_record(point, name_cache=None):
    # Only the bounded telemetry schema can use a native container dump.
    # Legacy/imported rows may have arbitrary size/fields and retain streaming.
    if (type(point) is not dict or len(point) != 2 or
            't' not in point or 'readings' not in point):
        return False
    stamp = point['t']
    readings = point['readings']
    if (type(stamp) is not int or not -9999999999 <= stamp <= 9999999999 or
            type(readings) is not list or len(readings) > 16):
        return False
    estimated = 64  # Object framing, timestamp and a generous whitespace margin.
    for reading in readings:
        if (type(reading) is not dict or len(reading) != 2 or
                'name' not in reading or 'percent' not in reading):
            return False
        name, percent = reading['name'], reading['percent']
        if type(name) is not str:
            return False
        size = name_cache.get(name) if name_cache is not None else None
        if size is None:
            if not 1 <= len(name) <= 48:
                return False
            try:
                size = len(name.encode('utf-8'))
            except UnicodeError:
                return False
            if size > 48 or any(ord(char) < 32 or char == '\x7f' for char in name):
                return False
            if name_cache is not None and len(name_cache) < HISTORY_NAME_CACHE_LIMIT:
                name_cache[name] = size
        if percent is not None and (type(percent) not in (int, float) or
                                    not 0 <= percent <= 100):
            return False  # NaN/Infinity must retain generic null normalization.
        # With controls excluded, JSON escaping is at most 3x UTF-8 bytes
        # (including surrogate-pair escaping on CPython). The fixed margin
        # covers keys, punctuation, whitespace and a finite numeric value.
        estimated += 64 + 3 * size
    return estimated <= MAX_HISTORY_RECORD


def _history_fragments(point, name_cache):
    if _small_history_record(point, name_cache):
        encoded = json.dumps(point).encode('utf-8')
        if len(encoded) <= MAX_HISTORY_RECORD:
            for offset in range(0, len(encoded), CHUNK):
                yield encoded[offset:offset + CHUNK]
            return
    yield from json_fragments(point)


def _json_string(value):
    if len(value) <= 32:
        # One native escape operation also emits the quotes. Even 32 astral
        # characters escaped as surrogate pairs fit in 386 bytes, below CHUNK.
        yield json.dumps(value).encode('utf-8')
        return
    # JSON escaping in small fragments avoids a proportional string allocation.
    yield b'"'
    for offset in range(0, len(value), 32):
        yield json.dumps(value[offset:offset + 32])[1:-1].encode('utf-8')
    yield b'"'


def json_fragments(value):
    if isinstance(value, str):
        yield from _json_string(value)
    elif isinstance(value, dict):
        yield b'{'
        first = True
        for key in value:
            if not first:
                yield b','
            first = False
            yield from _json_string(str(key))
            yield b':'
            yield from json_fragments(value[key])
        yield b'}'
    elif isinstance(value, (list, tuple, JsonArray)) or hasattr(value, '__next__'):
        iterator = iter(value.iterable if isinstance(value, JsonArray) else value)
        yield b'['
        first = True
        try:
            for item in iterator:
                if item is None and isinstance(value, JsonArray) and value.skip_none:
                    # An explicit cooperative checkpoint, not a JSON value.
                    # Flash history can yield it after each excluded/torn row.
                    yield b''
                    continue
                if not first:
                    yield b','
                first = False
                if isinstance(value, JsonArray) and value.history_records:
                    yield from _history_fragments(item, value._history_names)
                else:
                    yield from json_fragments(item)
        finally:
            if hasattr(iterator, 'close'):
                iterator.close()
        yield b']'
    elif value is None:
        yield b'null'
    elif value is True:
        yield b'true'
    elif value is False:
        yield b'false'
    elif isinstance(value, (float, int)):
        # JSON has no NaN/Infinity. Invalid telemetry is represented as null.
        encoded = str(value)
        yield b'null' if encoded.lower() in ('nan', '-nan', 'inf', '-inf', 'infinity', '-infinity') else encoded.encode()
    else:
        raise ValueError('Unsupported JSON response value')


class Response:
    def __init__(self, body=None, status=200, headers=None,
                 content_type='application/json', raw=False):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.content_type = content_type
        self.raw = raw


def file_chunks(path):
    with open(path, 'rb') as stream:
        while True:
            part = stream.read(CHUNK)
            if not part:
                break
            yield part


def response_fragments(response):
    reasons = {200: 'OK', 202: 'Accepted', 302: 'Found', 400: 'Bad Request',
               404: 'Not Found', 405: 'Method Not Allowed', 408: 'Request Timeout',
               409: 'Conflict', 413: 'Content Too Large', 415: 'Unsupported Media Type',
               431: 'Request Header Fields Too Large', 500: 'Internal Server Error',
               503: 'Service Unavailable'}
    # Close-delimited HTTP/1.0 permits one-pass JSON iterators without buffering
    # or counting. Browsers decode UTF-8 bytes until EOF; no character lengths.
    yield ('HTTP/1.0 %d %s\r\n' % (response.status, reasons.get(response.status, 'Response'))).encode()
    yield b'Connection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n'
    yield ('Content-Type: %s\r\n' % response.content_type).encode()
    for key in response.headers:
        value = str(response.headers[key])
        if '\r' in key + value or '\n' in key + value:
            raise ValueError('Invalid response header')
        yield ('%s: %s\r\n' % (key, value)).encode()
    yield b'\r\n'
    if response.raw:
        if isinstance(response.body, (str, bytes, bytearray)):
            data = response.body
            for offset in range(0, len(data), 96):
                part = data[offset:offset + 96]
                yield part.encode() if isinstance(part, str) else part
        else:
            iterator = iter(response.body)
            try:
                for fragment in iterator:
                    yield fragment
            finally:
                if hasattr(iterator, 'close'):
                    iterator.close()
    else:
        yield from json_fragments(response.body)


class Multipart:
    """Streaming multipart parser. Retains only a boundary plus part headers."""
    def __init__(self, content_type, factory):
        boundary = None
        for item in content_type.split(';')[1:]:
            pair = item.strip().split('=', 1)
            if len(pair) == 2 and pair[0].lower() == 'boundary':
                boundary = pair[1].strip('"').encode()
        if not boundary or len(boundary) > 70 or any(x < 33 or x > 126 for x in boundary):
            raise ValueError('Invalid multipart boundary')
        self.boundary = boundary
        self.marker = b'\r\n--' + boundary
        self.factory = factory
        self.buffer = bytearray()
        self.state = 'first'
        self.sink = None
        self.results = []
        self.count = 0

    def abort(self):
        if self.sink:
            try:
                self.sink.abort()
            finally:
                self.sink = None

    def _write_payload(self, count):
        for offset in range(0, count, CHUNK):
            self.sink.write(bytes(self.buffer[offset:min(count, offset + CHUNK)]))

    def feed(self, data):
        self.buffer.extend(data)
        # A received block is <=512 bytes, so this loop is bounded by that data.
        while True:
            if self.state == 'first':
                expected = b'--' + self.boundary + b'\r\n'
                if len(self.buffer) < len(expected):
                    return
                if self.buffer[:len(expected)] != expected:
                    raise ValueError('Malformed multipart opening')
                # MicroPython bytearrays support slice assignment, not deletion.
                self.buffer[:len(expected)] = b''
                self.state = 'headers'
            elif self.state == 'headers':
                end = self.buffer.find(b'\r\n\r\n')
                if end < 0:
                    if len(self.buffer) > 2048:
                        raise ValueError('Multipart headers too long')
                    return
                if end > 2048:
                    raise ValueError('Multipart headers too long')
                filename = None
                for line in bytes(self.buffer[:end]).decode().split('\r\n'):
                    if line.lower().startswith('content-disposition:'):
                        for attr in line.split(';')[1:]:
                            pair = attr.strip().split('=', 1)
                            if len(pair) == 2 and pair[0] == 'filename':
                                filename = pair[1].strip('"')
                if not filename or len(filename) > 96 or '/' in filename or '\\' in filename or filename in ('.', '..'):
                    raise ValueError('Expected a safe upload filename')
                self.count += 1
                if self.count > 32:
                    raise ValueError('Too many uploaded files')
                self.sink = self.factory(filename)
                self.buffer[:end + 4] = b''
                self.state = 'data'
            elif self.state == 'data':
                end = self.buffer.find(self.marker)
                if end < 0:
                    count = len(self.buffer) - len(self.marker) - 2
                    if count > 0:
                        self._write_payload(count)
                        self.buffer[:count] = b''
                    return
                if len(self.buffer) < end + len(self.marker) + 2:
                    return
                tail = bytes(self.buffer[end + len(self.marker):end + len(self.marker) + 2])
                if tail not in (b'--', b'\r\n'):
                    # Boundary-like payload bytes are ordinary file content.
                    count = end + 2
                    self._write_payload(count)
                    self.buffer[:count] = b''
                    continue
                if end:
                    self._write_payload(end)
                self.results.append(self.sink.finish())
                self.sink = None
                self.buffer[:end + len(self.marker) + 2] = b''
                self.state = 'done' if tail == b'--' else 'headers'
            else:
                if len(self.buffer) > 2 or (self.buffer and self.buffer not in (b'\r', b'\r\n')):
                    raise ValueError('Unexpected multipart epilogue')
                return

    def finish(self):
        if self.state != 'done' or self.sink is not None:
            raise ValueError('Truncated multipart upload')
        return {'ok': True, 'files': self.results, 'staged': True}


SETUP_HTML = '''<!doctype html><meta name="viewport" content="width=device-width"><title>Planter WiFi setup</title>
<style>body{font:18px system-ui;max-width:28em;margin:3em auto;padding:1em}input,button{display:block;padding:.7em;margin:.5em 0;width:90%}</style>
<h1>Connect your planter</h1><p>The Planter-Setup hotspot is open and has no password. The fields below are for your existing home Wi-Fi network so the planter can join it.</p><p>This garden controller supports 2.4 GHz Wi-Fi.</p>
<form method="post" action="/api/wifi"><label>Home Wi-Fi network name (SSID)<input name="ssid" maxlength="32" required autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false"></label>
<label>Home Wi-Fi password<input name="password" type="password" maxlength="63" autocomplete="new-password" placeholder="Leave blank if your home Wi-Fi has no password"></label><button>Save and restart</button></form>
<p>After saving, reconnect your phone to your home network. Watering continues during setup.</p><a href="/">Open dashboard</a>'''


class _Client:
    def __init__(self, sock, now):
        self.sock = sock
        self.started = now
        self.last_progress = now
        self.deadline = ticks_add(now, 15000)
        self.buffer = bytearray()
        self.headers = None
        self.length = 0
        self.received = 0
        self.multipart = None
        self.output = None
        self.pending = b''
        self.pending_offset = 0
        self.fragment = b''
        self.fragment_offset = 0
        self.complete = False
        self.checkpoint = False
        self.closed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        if self.multipart:
            try:
                self.multipart.abort()
            except Exception:
                pass
        if self.output and hasattr(self.output, 'close'):
            try:
                self.output.close()
            except Exception:
                pass


class HTTPServer:
    def __init__(self, handler, root='.', port=80, event=None,
                 upload_factory=None, redirect=None, load_shed=None):
        self.handler = handler
        self.root = root.rstrip('/\\')
        self.port = port
        self.event = event
        self.upload_factory = upload_factory
        self.redirect = redirect
        self.load_shed = load_shed
        self.listener = None
        self.clients = []
        self.state = 'stopped'
        self.last_error = None
        self.last_accept_ms = None
        self.last_response_ms = None
        self.accepted = 0
        self.completed = 0
        self.failed = 0
        self.restarts = 0
        self.next_restart = None

    def _event(self, message):
        if self.event:
            try:
                self.event('network', message)
            except Exception:
                pass

    def start(self):
        now = ticks_ms()
        if self.listener is not None:
            return self.state == 'listening'
        candidate = None
        try:
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.setblocking(False)
            candidate.bind(('0.0.0.0', self.port))
            candidate.listen(2)
            self.listener = candidate
            self.state = 'listening'
            self.next_restart = None
            self._event('HTTP listener started')
            return True
        except OSError as exc:
            if candidate:
                candidate.close()
            self._broken(exc, now)
            return False

    def _broken(self, exc, now):
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        self.listener = None
        self.state = 'fault'
        self.last_error = str(exc)[:160]
        self.next_restart = ticks_add(now, 30000)
        self._event('HTTP listener fault: ' + self.last_error)

    def close(self):
        for client in self.clients:
            client.close()
        self.clients = []
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass
        self.listener = None
        self.state = 'stopped'
        self.next_restart = None

    def health(self):
        return {'state': self.state, 'listening': self.state == 'listening',
                'clients': len(self.clients), 'accepted': self.accepted,
                'active_uploads': sum(1 for client in self.clients
                                      if client.multipart is not None and client.output is None and not client.closed),
                'completed': self.completed, 'failed': self.failed,
                'restarts': self.restarts, 'last_error': self.last_error,
                'last_accept_ms': self.last_accept_ms,
                'last_response_ms': self.last_response_ms,
                'reachability': 'unverified'}

    def _respond(self, client, response, now):
        client.buffer = bytearray()
        client.output = response_fragments(response)
        client.deadline = ticks_add(now, 120000)
        client.last_progress = now

    def _error(self, client, status, message, now):
        if client.multipart:
            client.multipart.abort()
        self._respond(client, Response({'error': message}, status), now)

    def _parse_headers(self, client, now):
        end = client.buffer.find(b'\r\n\r\n')
        if end < 0:
            if len(client.buffer) > MAX_HEADERS:
                self._error(client, 431, 'Headers too large', now)
            return False
        if end > MAX_HEADERS:
            self._error(client, 431, 'Headers too large', now)
            return False
        lines = bytes(client.buffer[:end]).decode('utf-8').split('\r\n')
        first = lines[0].split(' ')
        if len(first) != 3 or not first[2].startswith('HTTP/1.') or not first[1].startswith('/'):
            raise ValueError('Malformed request line')
        client.method = first[0]
        target = first[1].split('?', 1)
        client.path = unquote(target[0])
        client.query = parse_query(target[1] if len(target) == 2 else '')
        headers = {}
        for line in lines[1:]:
            pair = line.split(':', 1)
            if len(pair) != 2:
                raise ValueError('Malformed header')
            key, value = pair[0].strip().lower(), pair[1].strip()
            if key in headers:
                raise ValueError('Duplicate header')
            headers[key] = value
        if 'transfer-encoding' in headers:
            raise ValueError('Chunked request bodies are unsupported')
        raw_length = headers.get('content-length', '0')
        if not raw_length or len(raw_length) > 10 or not raw_length.isdigit():
            raise ValueError('Invalid Content-Length')
        client.length = int(raw_length)
        upload = client.path == '/api/upload' and client.method == 'POST'
        cap = MAX_UPLOAD if upload else MAX_BODY
        if self.redirect and self.redirect() and client.path == '/api/wifi':
            cap = 2048
        if client.length > cap:
            self._error(client, 413, 'Request body too large', now)
            return False
        if upload:
            if not self.upload_factory:
                self._error(client, 503, 'Uploads unavailable', now)
                return False
            content_type = headers.get('content-type', '')
            if not content_type.lower().startswith('multipart/form-data;'):
                self._error(client, 415, 'Multipart form required', now)
                return False
            client.multipart = Multipart(content_type, self.upload_factory)
            client.deadline = ticks_add(client.started, 120000)
        client.headers = headers
        client.buffer[:end + 4] = b''
        return True

    def _dispatch(self, client, now):
        stop_request = client.path == '/api/water/stop' or (client.path == '/api/valve' and client.query.get('state') == 'close')
        if self.load_shed and self.load_shed() and not stop_request and not client.multipart:
            response = Response({'error': 'Low network heap; retry shortly'}, 503, {'Retry-After': '5'})
        elif client.multipart:
            response = Response(client.multipart.finish())
        elif self.redirect and self.redirect() and client.path in PROBES:
            response = Response('', 302, {'Location': 'http://192.168.4.1/setup'}, 'text/plain', True)
        elif client.path == '/setup' and client.method == 'GET':
            response = Response(SETUP_HTML, content_type='text/html; charset=utf-8', raw=True)
        elif client.path == '/' and client.method == 'GET':
            path = self.root + '/index.html'
            headers = {}
            if 'gzip' in client.headers.get('accept-encoding', '').lower():
                try:
                    with open(path + '.gz', 'rb'):
                        pass
                    path += '.gz'
                    headers['Content-Encoding'] = 'gzip'
                    headers['Vary'] = 'Accept-Encoding'
                except OSError:
                    pass
            try:
                with open(path, 'rb'):
                    pass
                response = Response(file_chunks(path), headers=headers,
                                    content_type='text/html; charset=utf-8', raw=True)
            except OSError:
                response = Response(SETUP_HTML, content_type='text/html; charset=utf-8', raw=True)
        else:
            body = {}
            if client.buffer:
                content_type = client.headers.get('content-type', '').split(';', 1)[0].lower()
                if content_type == 'application/x-www-form-urlencoded':
                    body = parse_query(bytes(client.buffer).decode())
                elif content_type in ('application/json', ''):
                    body = json.loads(bytes(client.buffer).decode())
                else:
                    self._error(client, 415, 'Expected JSON or form data', now)
                    return
            result = self.handler(client.method, client.path, client.query, body, client.headers)
            response = result if isinstance(result, Response) else Response(result)
        self._respond(client, response, now)

    def _read(self, client, now):
        try:
            data = client.sock.recv(CHUNK)
        except OSError as exc:
            if errno(exc) in WOULD_BLOCK:
                return
            raise
        if not data:
            client.close()
            return
        client.last_progress = now
        if client.headers is None:
            client.buffer.extend(data)
            if not self._parse_headers(client, now):
                return
            data = bytes(client.buffer)
            client.buffer = bytearray()
        if client.received + len(data) > client.length:
            raise ValueError('Body exceeds Content-Length')
        client.received += len(data)
        if client.multipart:
            client.multipart.feed(data)
        else:
            client.buffer.extend(data)
        if client.received == client.length:
            self._dispatch(client, now)

    def _write(self, client, now):
        # Fragment generation, not just socket bytes, consumes Python time.
        # Keep each client slice short before returning to valve safety.
        for unused in range(WRITES_PER_POLL):
            if client.pending_offset >= len(client.pending):
                packed = bytearray()
                for fragment_count in range(FRAGMENTS_PER_WRITE):
                    if client.fragment_offset >= len(client.fragment):
                        try:
                            fragment = next(client.output)
                            client.fragment = fragment.encode() if isinstance(fragment, str) else fragment
                            client.fragment_offset = 0
                            if not client.fragment:
                                client.checkpoint = True
                                # Server-side flash progress is not a stalled
                                # peer. The absolute response deadline remains.
                                client.last_progress = now
                                break
                        except StopIteration:
                            client.complete = True
                            break
                    count = min(CHUNK - len(packed), len(client.fragment) - client.fragment_offset)
                    packed.extend(memoryview(client.fragment)[client.fragment_offset:client.fragment_offset + count])
                    client.fragment_offset += count
                    if len(packed) == CHUNK:
                        break
                client.pending = packed
                client.pending_offset = 0
                if not packed:
                    client.checkpoint = False
                    if client.complete:
                        self.completed += 1
                        self.last_response_ms = now
                        client.close()
                    return
            try:
                sent = client.sock.send(memoryview(client.pending)[client.pending_offset:])
            except OSError as exc:
                if errno(exc) in WOULD_BLOCK:
                    return
                raise
            if sent is None or sent == 0:
                return
            if sent < 0 or sent > len(client.pending) - client.pending_offset:
                raise OSError('Invalid socket send result')
            client.pending_offset += sent
            client.last_progress = now
            if client.checkpoint:
                if client.pending_offset >= len(client.pending):
                    client.checkpoint = False
                return

    def poll(self, now_ms=None):
        now = ticks_ms() if now_ms is None else now_ms
        if self.state == 'fault' and self.next_restart is not None and ticks_diff(now, self.next_restart) >= 0:
            self.restarts += 1
            self.start()
        if self.listener is not None and len(self.clients) < 2:
            try:
                connection, address = self.listener.accept()
                try:
                    connection.setblocking(False)
                    self.clients.append(_Client(connection, now))
                    self.accepted += 1
                    self.last_accept_ms = now
                except Exception:
                    connection.close()
                    raise
            except OSError as exc:
                # Aborted embryonic connections do not invalidate the listener.
                if errno(exc) not in WOULD_BLOCK + (4, 103, 113):
                    self._broken(exc, now)
        for client in self.clients:
            if client.closed:
                continue
            if ticks_diff(now, client.deadline) >= 0 or ticks_diff(now, client.last_progress) >= 10000:
                self.failed += 1
                client.close()
                continue
            try:
                if client.output is None:
                    self._read(client, now)
                elif not client.closed:
                    self._write(client, now)
            except (ValueError, UnicodeError, RuntimeError) as exc:
                self.failed += 1
                if client.output is None:
                    try:
                        self._error(client, 400, str(exc)[:120], now)
                    except Exception:
                        client.close()
                else:
                    client.close()
            except Exception as exc:
                self.failed += 1
                self.last_error = str(exc)[:160]
                client.close()
        self.clients = [client for client in self.clients if not client.closed]
