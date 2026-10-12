"""The real urllib transport, against a local HTTP server (no internet needed)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from serato_analyzer.artwork.http import (
    HttpClient, HttpError, NetworkError, ResponseTooLarge, UnsafeUrlError, UrllibTransport,
)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/ok":
            body = b'{"hello": "world"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/big-declared":
            self.send_response(200)
            self.send_header("Content-Length", str(10_000_000))
            self.end_headers()
        elif self.path == "/big-chunked":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 50_000)
        elif self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
        elif self.path == "/to-ftp":
            self.send_response(302)
            self.send_header("Location", "ftp://example.com/cover.jpg")
            self.end_headers()
        elif self.path == "/to-ok":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def http(monkeypatch):
    # Talk to the local server directly even if the environment sets a proxy.
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    return HttpClient(UrllibTransport(), max_retries=0, default_interval=0)


def test_get_json(server, http):
    assert http.get_json(f"{server}/ok") == (200, {"hello": "world"})


def test_status_passthrough(server, http):
    assert http.get(f"{server}/missing", accept_status=(200, 404)).status == 404


def test_size_caps(server, http):
    with pytest.raises(ResponseTooLarge):
        http.get(f"{server}/big-declared", max_bytes=1000)
    with pytest.raises(ResponseTooLarge):
        http.get(f"{server}/big-chunked", max_bytes=1000)


def test_redirects(server, http):
    assert http.get(f"{server}/to-ok").status == 200
    # urllib itself refuses file:// redirects; our handler also refuses ftp://.
    with pytest.raises(HttpError):
        http.get(f"{server}/to-file")
    with pytest.raises(UnsafeUrlError):
        http.get(f"{server}/to-ftp")


def test_connection_refused_is_network_error(http):
    with pytest.raises(NetworkError):
        http.get("http://127.0.0.1:9/nothing")
