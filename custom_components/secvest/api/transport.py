"""HTTP handling for the panel.

One HTTPS connection per panel with TLS session resumption (ADR 0001),
strictly sequential requests through one queue, and the mapping of the
panel's responses and connection problems to results and typed errors.
"""

import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
import heapq
import http.client
import itertools
import json
import logging
import select
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

from .errors import (
    ArmingBlockedError,
    AuthenticationError,
    CommunicationError,
    ConnectionLostError,
    InstallerLockedError,
    InvalidRequestError,
    NotAllowedError,
    NotFoundError,
)
from .parsing import loads, parse_faults

_LOGGER = logging.getLogger(__name__)


def basic_auth(user_code: str, password: str) -> str:
    """Return the Authorization header value.

    The panel sends no WWW-Authenticate challenge, so the header has to be
    sent with every request.
    """
    token = b64encode(f"{user_code}:{password}".encode()).decode("ascii")
    return f"Basic {token}"


def check_response(status: int, body: bytes) -> Any:
    """Return the decoded JSON of a 200 response or raise the matching error."""
    if status == http.client.OK:
        return loads(body)
    if status == http.client.BAD_REQUEST:
        raise InvalidRequestError("the panel rejected a value in the request")
    if status == http.client.UNAUTHORIZED:
        raise AuthenticationError("the panel rejected the credentials")
    if status == http.client.FORBIDDEN:
        raise _forbidden(body)
    if status == http.client.NOT_FOUND:
        raise NotFoundError("unknown path, partition or zone")
    if status == http.client.CONFLICT:
        raise _arming_blocked(body)
    raise CommunicationError(f"unexpected response status {status}")


@contextmanager
def translate_errors() -> Iterator[None]:
    """Turn timeouts and connection problems into CommunicationError."""
    try:
        yield
    except (TimeoutError, OSError, http.client.HTTPException) as err:
        raise CommunicationError(
            f"connection to the panel failed: {type(err).__name__}"
        ) from err


def _forbidden(body: bytes) -> Exception:
    # an empty body means "not allowed", {"state":"installer"} the lock
    if not body.strip():
        return NotAllowedError("the panel refused the action")
    data = loads(body)
    if isinstance(data, dict) and data.get("state") == "installer":
        return InstallerLockedError("the installer is logged in at the panel")
    return CommunicationError("unexpected 403 response")


def _arming_blocked(body: bytes) -> ArmingBlockedError:
    # the refusal counts even if its fault list can't be read
    try:
        faults = tuple(parse_faults(loads(body)))
    except CommunicationError:
        _LOGGER.warning("Could not read the faults of a 409 response")
        faults = ()
    return ArmingBlockedError(faults)


CONNECT_TIMEOUT = 15.0  # covers the panel's 6.5 s full TLS handshake
READ_TIMEOUT = 10.0
LOG_READ_TIMEOUT = 30.0  # log requests take about 6 s
# reconnect instead of reusing a connection idle for longer; the panel
# closes idle connections after 10-30 s, and a resumed reconnect is cheap
MAX_IDLE = 5.0
# the integration passes its version; users may override it (e.g. for a proxy)
DEFAULT_USER_AGENT = "ha-secvest"

_CLOSED_BY_PEER = (
    http.client.RemoteDisconnected,
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
)


class _NotSentError(ConnectionError):
    """The connection broke before the request was sent completely."""


@dataclass
class TransportStats:
    """Connection counters for diagnostics."""

    requests: int = 0
    connections: int = 0
    full_handshakes: int = 0
    resumed_handshakes: int = 0
    reconnects: int = 0
    last_connect_time: float | None = None


class _Connection(http.client.HTTPSConnection):
    """HTTPS connection that resumes the transport's TLS session."""

    def __init__(self, transport: Transport, timeout: float) -> None:
        super().__init__(
            transport.host,
            transport.port,
            timeout=timeout,
            context=transport.ssl_context,
        )
        self._transport = transport

    def connect(self) -> None:
        transport = self._transport
        started = time.monotonic()
        sock = socket.create_connection((self.host, self.port), CONNECT_TIMEOUT)
        try:
            sock.settimeout(CONNECT_TIMEOUT)
            # an unusable session is ignored and a full handshake follows
            tls = transport.ssl_context.wrap_socket(
                sock, server_hostname=self.host, session=transport.tls_session
            )
        except BaseException:
            sock.close()
            raise
        tls.settimeout(self.timeout)
        self.sock = tls
        transport.on_connected(tls, time.monotonic() - started)


class _RequestQueue:
    """Grants the connection to one holder at a time; commands go first."""

    def __init__(self) -> None:
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._order = itertools.count()

    async def acquire(self, priority: bool) -> None:
        if not self._locked and not self._waiters:
            self._locked = True
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (0 if priority else 1, next(self._order), future))
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                # granted just before the cancellation: pass it on
                self.release()
            raise

    def release(self) -> None:
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)
                return
        self._locked = False


class Transport:
    """The single, sequential HTTPS connection to one panel.

    Blocking http.client calls run in a dedicated thread, because asyncio
    can't resume TLS sessions (ADR 0001). Requests are strictly
    sequential; callers can hold the queue for a sequence of requests.
    """

    def __init__(
        self,
        url: str,
        user_code: str,
        password: str,
        *,
        verify_ssl: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        """Prepare the connection; nothing is sent yet."""
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("the panel's address must be an https URL")
        self.host = parts.hostname
        self.port = parts.port or 443
        self._base_path = parts.path.rstrip("/")
        self._authorization = basic_auth(user_code, password)
        self._user_agent = user_agent or DEFAULT_USER_AGENT
        self._verify_ssl = verify_ssl
        self._ssl_context: ssl.SSLContext | None = None
        self.tls_session: ssl.SSLSession | None = None
        self.stats = TransportStats()
        # the last answer to each read, for diagnostics (#43); the log is
        # left out, it is large and names people
        self.last_responses: dict[str, Any] = {}
        self._conn: _Connection | None = None
        self._last_used = 0.0
        self._queue = _RequestQueue()
        self._holder: asyncio.Task[Any] | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="secvest")
        # set by a 401; failed logins may count towards a code tamper alarm,
        # so nothing is sent with these credentials again. New credentials
        # mean a new transport.
        self._auth_failed = False
        # set by close(); a late refresh or command must not reach the
        # stopped thread
        self._closed = False

    @property
    def ssl_context(self) -> ssl.SSLContext:
        """TLS settings, created on first use in the transport's thread.

        Loading the CA certificates is blocking I/O, which must not run in
        the event loop.
        """
        if self._ssl_context is None:
            context = ssl.create_default_context()
            if not self._verify_ssl:
                # the panel uses a self-signed certificate
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            self._ssl_context = context
        return self._ssl_context

    @asynccontextmanager
    async def hold(self, *, priority: bool = False) -> AsyncIterator[None]:
        """Hold the queue for a sequence of requests, e.g. a command.

        With priority (user commands), the hold is granted before pending
        polling requests, but never interrupts a running request.
        """
        task = asyncio.current_task()
        if task is not None and task is self._holder:
            yield
            return
        await self._queue.acquire(priority)
        self._holder = task
        try:
            yield
        finally:
            self._holder = None
            self._queue.release()

    async def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        read_timeout: float = READ_TIMEOUT,
    ) -> Any:
        """Send one request and return the decoded JSON of the answer."""
        async with self.hold():
            if self._closed:
                raise CommunicationError("no request sent: the connection is closed")
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._executor, self._request_sync, method, path, body, read_timeout
            )

    async def close(self) -> None:
        """Close the connection and stop the thread; later requests fail."""
        async with self.hold(priority=True):
            if self._closed:
                return
            self._closed = True
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._disconnect)
        self._executor.shutdown(wait=True)

    def on_connected(self, tls: ssl.SSLSocket, duration: float) -> None:
        """Count the handshake and keep the session for the next one."""
        stats = self.stats
        if stats.connections:
            stats.reconnects += 1
        stats.connections += 1
        if tls.session_reused:
            stats.resumed_handshakes += 1
        else:
            stats.full_handshakes += 1
        stats.last_connect_time = duration
        self.tls_session = tls.session

    # blocking part, runs in the transport's thread

    @property
    def authentication_failed(self) -> bool:
        """Whether the panel rejected the credentials; no request is sent then."""
        return self._auth_failed

    def _request_sync(
        self, method: str, path: str, body: dict[str, Any] | None, timeout: float
    ) -> Any:
        if self._auth_failed:
            # also stops requests that were queued before the 401
            raise AuthenticationError(
                "no request sent: the panel rejected these credentials before"
            )
        data = None if body is None else json.dumps(body).encode()
        headers = {
            "Authorization": self._authorization,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
        }
        with translate_errors():
            try:
                status, payload = self._attempt(method, path, data, headers, timeout)
            except (_NotSentError, *_CLOSED_BY_PEER):
                # closed by the panel before the request went out, or before
                # a read was answered (a command raised ConnectionLostError)
                _LOGGER.debug("Connection closed by the panel, reconnecting")
                status, payload = self._attempt(method, path, data, headers, timeout)
        if status == http.client.UNAUTHORIZED:
            self._auth_failed = True
            self._disconnect()
            _LOGGER.warning(
                "The panel rejected the credentials; no further requests are sent "
                "until they are entered again"
            )
        result = check_response(status, payload)
        if method == "GET" and not path.startswith("/logs/"):
            self.last_responses[path] = result
        return result

    def _attempt(
        self,
        method: str,
        path: str,
        data: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        """Exchange once; a command that broke off after going out is lost.

        The panel may or may not have received it, so it is never sent
        again here (the coordinator verifies, then decides).
        """
        try:
            return self._exchange(method, path, data, headers, timeout)
        except _CLOSED_BY_PEER as err:
            if method != "GET":
                raise ConnectionLostError(
                    "the connection broke after the command was sent"
                ) from err
            raise

    def _exchange(
        self,
        method: str,
        path: str,
        data: bytes | None,
        headers: dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        conn = self._connection(timeout)
        self.stats.requests += 1
        try:
            try:
                conn.request(method, self._base_path + path, body=data, headers=headers)
            except _CLOSED_BY_PEER as err:
                raise _NotSentError from err
            response = conn.getresponse()
            payload = response.read()
        except BaseException:
            self._disconnect()
            raise
        self._last_used = time.monotonic()
        if conn.sock is not None:
            # TLS 1.3 sends its tickets after the handshake
            self.tls_session = conn.sock.session
        if response.will_close:
            self._disconnect()
        return response.status, payload

    def _connection(self, timeout: float) -> _Connection:
        conn = self._conn
        if conn is not None and (
            time.monotonic() - self._last_used > MAX_IDLE or _closed(conn)
        ):
            self._disconnect()
            conn = None
        if conn is None:
            conn = _Connection(self, timeout)
            conn.connect()
            self._conn = conn
        elif conn.sock is not None:
            conn.sock.settimeout(timeout)
        conn.timeout = timeout
        return conn

    def _disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _closed(conn: http.client.HTTPSConnection) -> bool:
    """Whether the panel has closed the idle connection (EOF is readable)."""
    sock = conn.sock
    if sock is None:
        return True
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except OSError, ValueError:
        return True
    return bool(readable)
