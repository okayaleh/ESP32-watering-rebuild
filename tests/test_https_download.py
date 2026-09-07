"""Real loopback TLS failures and MicroPython nonblocking stream behavior."""
import hashlib
from pathlib import Path
import select
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from updater import HTTPDownload, _tls_context, _url
from test_updater import FakeSocket, FakePoll, SocketModule

FIXTURES = Path(__file__).parent / "fixtures/tls"


class RealPoll:
    def register(self, sock, events):
        self.sock, self.events = sock, events
    modify = register
    def poll(self, timeout):
        assert timeout == 0
        # CPython SSL.pending is equivalent to MicroPython's SSL POLLIN path.
        if self.events & 1 and isinstance(self.sock, ssl.SSLSocket) and self.sock.pending():
            return [(self.sock, 1)]
        ready = select.select([self.sock] if self.events & 1 else [],
                              [self.sock] if self.events & 4 else [], [self.sock], 0)
        return [(self.sock, 1)] if any(ready) else []


class RedirectedSocket:
    """Only test routing changes: production URL/hostname validation still runs."""
    def __init__(self, port):
        self.sock = socket.socket()
        self.port = port
    def connect(self, address):
        assert address == ("127.0.0.1", 443)
        return self.sock.connect(("127.0.0.1", self.port))
    def __getattr__(self, key):
        return getattr(self.sock, key)


class HTTPSDownloadTests(unittest.TestCase):
    def test_https_origin_and_header_injection_rejected_before_connect(self):
        for url in ("https://github.com/a", "https://raw.githubusercontent.com.evil/a",
                    "https://raw.githubusercontent.com:444/a", "https://user@raw.githubusercontent.com/a",
                    "https://raw.githubusercontent.com/hello\r\nInjected: bad", "https://RAW.githubusercontent.com/a",
                    "https://raw.githubusercontent.com\\evil/a", "https://raw.githubusercontent.com/a\x00",
                    "ftp://raw.githubusercontent.com/a", "http://bad%2ehost/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _url(url)
        self.assertEqual(_url("https://raw.githubusercontent.com/a"), ("raw.githubusercontent.com", 443, "/a"))

    def test_production_context_requires_certificates_and_hostname(self):
        context = _tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(len(context.get_ca_certs()), 3)

    def test_micropython_none_retries_deferred_handshake_and_closes_raw(self):
        data = b"verified body" * 90
        raw = FakeSocket(b"")
        class TLSStream:
            def __init__(self):
                self.response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" + data
                self.writes = self.reads = 0
                self.closed = False
            def write(self, chunk):
                assert len(chunk) <= 512
                self.writes += 1
                return None if self.writes < 4 else len(chunk)
            def read(self, count):
                assert count <= 512
                self.reads += 1
                if self.reads % 2: return None
                result, self.response = self.response[:count], self.response[count:]
                return result
            def close(self): self.closed = True
        stream = TLSStream()
        seen = []
        class Context:
            def wrap_socket(self, sock, **kwargs):
                seen.append((sock, kwargs))
                return stream
        with tempfile.TemporaryDirectory() as root:
            target = Path(root)/"stage"
            dl = HTTPDownload("https://raw.githubusercontent.com/a", str(target), 4096, 0,
                resolver=lambda host: "127.0.0.1", socket_module=SocketModule(raw),
                poll_factory=FakePoll, tls_factory=Context)
            for tick in range(1000):
                dl.poll(tick)
                if dl.done or dl.error: break
            self.assertTrue(dl.done, dl.error)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(seen, [(raw, {"server_hostname":"raw.githubusercontent.com", "do_handshake_on_connect":False})])
            self.assertTrue(raw.closed and stream.closed)
            self.assertIsNone(dl.context)
            self.assertIsNone(dl.poller)

    def test_handshake_stall_uses_absolute_wrap_safe_deadline(self):
        raw = FakeSocket(b"")
        stream = SimpleNamespace(write=lambda data: None, close=lambda: None)
        context = SimpleNamespace(wrap_socket=lambda *args, **kw: stream)
        with tempfile.TemporaryDirectory() as root:
            start = (1 << 30) - 30
            target = Path(root)/"stage"
            dl = HTTPDownload("https://raw.githubusercontent.com/a", str(target), 4096, start,
                resolver=lambda host: "127.0.0.1", socket_module=SocketModule(raw),
                poll_factory=FakePoll, tls_factory=lambda: context, timeout_ms=100)
            for tick in range(105): dl.poll((start + tick) % (1 << 30))
            self.assertIn("deadline", dl.error)
            self.assertTrue(raw.closed)
            self.assertFalse(target.exists())

    def real_transfer(self, case):
        response = b"HTTP/1.1 200 OK\r\nContent-Length: 1500\r\n\r\n" + b"a"*1500
        if case == "redirect": response = b"HTTP/1.1 302 Found\r\nLocation: http://example.com/\r\nContent-Length: 1\r\n\r\nx"
        if case == "truncate": response = response[:-20]
        if case == "compressed": response = b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Encoding: gzip\r\n\r\nx"
        listener = socket.socket()
        listener.bind(("127.0.0.1",0));listener.listen(1);listener.settimeout(5)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(FIXTURES/("expired.pem" if case == "expired" else "server.pem"))
        server_errors = []
        def serve():
            try:
                with listener.accept()[0] as peer:
                    peer.settimeout(3)
                    with server_context.wrap_socket(peer,server_side=True) as tls:
                        request = b""
                        while b"\r\n\r\n" not in request:
                            request += tls.recv(512)
                        # Multiple records exercise TLS-buffered reads even
                        # when the underlying socket has no new readable data.
                        for offset in range(0,len(response),173): tls.sendall(response[offset:offset+173])
            except (OSError, ssl.SSLError) as exc:
                server_errors.append(str(exc))
        thread = threading.Thread(target=serve,daemon=True);thread.start()
        def context_factory():
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if case != "untrusted": context.load_verify_locations(cafile=str(FIXTURES/"ca.pem"))
            class Wrapper:
                def wrap_socket(self, raw, **kwargs):
                    if case == "wrong_hostname": kwargs["server_hostname"] = "invalid.example"
                    return context.wrap_socket(raw.sock, **kwargs)
            return Wrapper()
        try:
            with tempfile.TemporaryDirectory() as root:
                target = Path(root)/"stage"
                started = time.monotonic()
                dl = HTTPDownload("https://raw.githubusercontent.com/a", str(target), 4096, 0,
                    resolver=lambda host: "127.0.0.1", timeout_ms=5000, poll_factory=RealPoll,
                    socket_module=SimpleNamespace(socket=lambda: RedirectedSocket(listener.getsockname()[1])),
                    tls_factory=context_factory)
                while not dl.done and not dl.error:
                    dl.poll(int((time.monotonic()-started)*1000))
                    time.sleep(0.0005)
                if case == "valid":
                    self.assertTrue(dl.done, dl.error)
                    self.assertEqual(target.read_bytes(),b"a"*1500)
                    self.assertEqual(dl.hash.digest(),hashlib.sha256(b"a"*1500).digest())
                else:
                    self.assertTrue(dl.error, case)
                    self.assertFalse(dl.done)
                    self.assertFalse(target.exists())
                    self.assertNotIn("deadline",dl.error)
                self.assertIsNone(dl.sock)
                self.assertIsNone(dl.raw_sock)
        finally:
            listener.close();thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

    def test_real_tls_valid_and_fail_closed(self):
        for case in ("valid", "untrusted", "wrong_hostname", "expired", "redirect", "truncate", "compressed"):
            with self.subTest(case=case): self.real_transfer(case)


if __name__ == "__main__": unittest.main()
