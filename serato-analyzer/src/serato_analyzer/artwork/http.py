"""HTTP access for artwork providers.

* Only ``http``/``https`` URLs are fetched, including after redirects.
* Every response body is size-capped while streaming.
* Requests to each host are rate limited (MusicBrainz asks for at most one
  request per second) and ``429``/``503`` responses honour ``Retry-After``.
* The transport is injectable so unit tests never touch the network.
"""

from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

from .. import __version__

DEFAULT_USER_AGENT = f"SeratoAnalyzer/{__version__} ( https://github.com/deanhome/deanhome )"
ALLOWED_SCHEMES = ("http", "https")


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class NetworkError(HttpError):
    """DNS failure, refused connection, timeout, TLS failure, ..."""


class RateLimitedError(HttpError):
    pass


class ResponseTooLarge(HttpError):
    pass


class UnsafeUrlError(HttpError):
    pass


@dataclass
class Response:
    status: int
    body: bytes
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        name = name.lower()
        for k, v in self.headers.items():
            if k.lower() == name:
                return v
        return None

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HttpError(f"Invalid JSON from {self.url}: {exc}", self.status) from exc


class Transport(Protocol):
    def get(self, url: str, headers: Mapping[str, str], timeout: float, max_bytes: int) -> Response:
        ...


def check_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.netloc:
        raise UnsafeUrlError(f"Only http(s) URLs can be fetched: {url!r}")
    return url.strip()


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:  # python.org macOS builds ship without system CA certificates
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


class UrllibTransport:
    """Standard-library transport (no extra dependency to package)."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(
            _SafeRedirectHandler(), urllib.request.HTTPSHandler(context=_ssl_context()))

    def get(self, url: str, headers: Mapping[str, str], timeout: float, max_bytes: int) -> Response:
        req = urllib.request.Request(check_url(url), headers=dict(headers), method="GET")
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                return Response(resp.status, _read_capped(resp, max_bytes, url), resp.geturl(),
                                dict(resp.headers.items()))
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read(64 * 1024)
            except Exception:
                pass
            return Response(exc.code, body, url, dict(exc.headers.items()) if exc.headers else {})
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NetworkError(f"Network error fetching {urlsplit(url).netloc}: {reason}") from exc


def _read_capped(resp, max_bytes: int, url: str) -> bytes:
    length = resp.headers.get("Content-Length")
    if length and length.isdigit() and int(length) > max_bytes:
        raise ResponseTooLarge(f"{url} is {int(length)} bytes; the limit is {max_bytes}")
    chunks, total = [], 0
    while True:
        chunk = resp.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLarge(f"{url} exceeded the {max_bytes}-byte download limit")
        chunks.append(chunk)
    return b"".join(chunks)


class RateLimiter:
    """Minimum spacing between requests, shared across threads."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._clock()
            self._next = max(now, self._next) + self.min_interval

    def defer(self, seconds: float) -> None:
        with self._lock:
            self._next = max(self._next, self._clock() + seconds)


class HttpClient:
    def __init__(self, transport: Transport | None = None, user_agent: str = DEFAULT_USER_AGENT,
                 timeout: float = 15.0, max_retries: int = 2, max_retry_wait: float = 30.0,
                 host_intervals: Mapping[str, float] | None = None, default_interval: float = 0.25,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.transport = transport or UrllibTransport()
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_retry_wait = max_retry_wait
        self._intervals = dict(host_intervals or {})
        self._default_interval = default_interval
        self._clock, self._sleep = clock, sleep
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()
        self.request_log: list[str] = []  # handy for diagnostics and tests

    def set_host_interval(self, host: str, seconds: float) -> None:
        """Set a minimum spacing for *host*, keeping any stricter existing value."""
        with self._lock:
            current = self._intervals.get(host, 0.0)
            self._intervals[host] = max(current, seconds)
            if host in self._limiters:
                self._limiters[host].min_interval = self._intervals[host]

    def limiter_for(self, host: str) -> RateLimiter:
        with self._lock:
            if host not in self._limiters:
                interval = self._intervals.get(host, self._default_interval)
                self._limiters[host] = RateLimiter(interval, self._clock, self._sleep)
            return self._limiters[host]

    def get(self, url: str, headers: Mapping[str, str] | None = None,
            max_bytes: int = 2 * 1024 * 1024, accept_status: tuple[int, ...] = (200,)) -> Response:
        url = check_url(url)
        host = urlsplit(url).netloc.lower()
        hdrs = {"User-Agent": self.user_agent, "Accept": "application/json, image/*;q=0.9"}
        hdrs.update(headers or {})
        limiter = self.limiter_for(host)
        attempt = 0
        while True:
            limiter.wait()
            self.request_log.append(url)
            try:
                resp = self.transport.get(url, hdrs, self.timeout, max_bytes)
            except NetworkError:
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                self._sleep(min(2.0 ** attempt, self.max_retry_wait))
                continue
            if resp.status in accept_status:
                return resp
            if resp.status in (429, 503):
                retry_after = _parse_retry_after(resp.header("Retry-After"))
                wait = retry_after if retry_after is not None else 2.0 ** (attempt + 1)
                if attempt >= self.max_retries or wait > self.max_retry_wait:
                    raise RateLimitedError(f"{host} is rate limiting requests (HTTP {resp.status})",
                                           resp.status, retry_after)
                limiter.defer(wait)
                attempt += 1
                continue
            raise HttpError(f"HTTP {resp.status} from {host}", resp.status)

    def get_json(self, url: str, headers: Mapping[str, str] | None = None,
                 accept_status: tuple[int, ...] = (200,)) -> tuple[int, object]:
        resp = self.get(url, headers, accept_status=accept_status)
        if resp.status != 200:
            return resp.status, None
        return resp.status, resp.json()


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone

        when = parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None
