"""Tests for the connection to the panel (#2), against the fake panel."""

import asyncio
from collections.abc import AsyncIterator
import logging
import ssl
import time

import pytest

from custom_components.secvest.api import transport as transport_module
from custom_components.secvest.api.errors import (
    ArmingBlockedError,
    AuthenticationError,
    CommunicationError,
    ConnectionLostError,
    InstallerLockedError,
    NotFoundError,
)
from custom_components.secvest.api.transport import Transport, basic_auth

from ..fake_panel import FakePanel, Injection


@pytest.fixture
async def transport(fake_panel: FakePanel) -> AsyncIterator[Transport]:
    """Return a transport connected to the fake panel."""
    transport = Transport(fake_panel.url, fake_panel.user_code, fake_panel.password)
    yield transport
    await transport.close()


async def test_request(fake_panel: FakePanel, transport: Transport) -> None:
    """A request is answered with the decoded JSON."""
    data = await transport.request("GET", "/system/")
    assert data["partitions"] == ["1", "2", "3", "4"]
    assert transport.stats.full_handshakes == 1


async def test_keep_alive_within_a_round(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """Requests in quick succession share one connection."""
    for path in ("/system/partitions/", "/alarms/", "/faults/"):
        await transport.request("GET", path)
    assert fake_panel.stats.connections == 1


async def test_resumes_after_the_panel_closed_the_connection(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """A reconnect after an idle close resumes the TLS session."""
    fake_panel.idle_timeout = 0.2
    await transport.request("GET", "/system/")
    await asyncio.sleep(0.5)
    await transport.request("GET", "/system/")
    assert fake_panel.stats.full_handshakes == 1
    assert fake_panel.stats.resumed_handshakes == 1
    assert transport.stats.resumed_handshakes == 1
    assert transport.stats.reconnects == 1
    # the closed connection was noticed before sending
    assert transport.stats.requests == 2


async def test_reconnects_after_max_idle(
    fake_panel: FakePanel, transport: Transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection idle for too long is replaced before it is used."""
    monkeypatch.setattr(transport_module, "MAX_IDLE", 0.1)
    await transport.request("GET", "/system/")
    await asyncio.sleep(0.3)
    await transport.request("GET", "/system/")
    assert fake_panel.stats.connections == 2
    assert fake_panel.stats.resumed_handshakes == 1


async def test_full_handshake_if_the_session_is_refused(
    fake_panel: FakePanel,
    transport: Transport,
    panel_certificate: tuple[object, object],
) -> None:
    """If the panel doesn't accept the session, a full handshake follows."""
    fake_panel.idle_timeout = 0.2
    await transport.request("GET", "/system/")
    # new ticket keys, as after a panel restart
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(*panel_certificate)  # type: ignore[arg-type]
    fake_panel._context = context
    await asyncio.sleep(0.5)
    await transport.request("GET", "/system/")
    assert fake_panel.stats.full_handshakes == 2
    assert transport.stats.resumed_handshakes == 0


async def test_requests_are_sequential(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """Concurrent callers never cause parallel requests."""
    fake_panel.log_delay = 0.1
    await asyncio.gather(
        *(transport.request("GET", "/logs/") for _ in range(3)),
        *(transport.request("GET", "/faults/") for _ in range(3)),
    )
    assert len(fake_panel.stats.requests) == 6
    assert fake_panel.stats.max_open_connections == 1


async def test_commands_go_first(fake_panel: FakePanel, transport: Transport) -> None:
    """A command is placed ahead of pending polling requests."""
    fake_panel.log_delay = 0.3
    running = asyncio.create_task(transport.request("GET", "/logs/"))
    await asyncio.sleep(0.1)
    polling = asyncio.create_task(transport.request("GET", "/faults/"))
    await asyncio.sleep(0)

    async def command() -> None:
        async with transport.hold(priority=True):
            await transport.request("PUT", "/system/partitions-1/", {"state": "set"})
            await transport.request("GET", "/system/partitions-1/")

    await asyncio.gather(running, polling, command())
    assert [path for _, path in fake_panel.stats.requests] == [
        "/logs/",
        "/system/partitions-1/",
        "/system/partitions-1/",
        "/faults/",
    ]


async def test_cancelled_waiter_releases_the_queue(transport: Transport) -> None:
    """A cancelled request doesn't block the queue."""
    async with transport.hold():
        waiting = asyncio.create_task(transport.request("GET", "/faults/"))
        await asyncio.sleep(0)
        waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert await transport.request("GET", "/faults/") == []


async def test_get_retried_once_if_closed_before_the_answer(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """A read is repeated once on a fresh connection."""
    fake_panel.inject(Injection("GET", "/faults/", "drop_before"))
    assert await transport.request("GET", "/faults/") == []
    assert transport.stats.requests == 2


async def test_get_not_retried_twice(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """A second broken connection is a communication error."""
    fake_panel.inject(Injection("GET", "/faults/", "drop_before", times=2))
    with pytest.raises(CommunicationError):
        await transport.request("GET", "/faults/")
    assert transport.stats.requests == 2


@pytest.mark.parametrize("action", ["drop_before", "drop_after"])
async def test_command_not_retried_after_sending(
    fake_panel: FakePanel, transport: Transport, action: str
) -> None:
    """A command whose answer is lost is never repeated by the transport."""
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", action))  # type: ignore[arg-type]
    with pytest.raises(ConnectionLostError):
        await transport.request("PUT", "/system/partitions-1/", {"state": "set"})
    assert [method for method, _ in fake_panel.stats.requests] == ["PUT"]


async def test_timeout(fake_panel: FakePanel, transport: Transport) -> None:
    """A timeout is a communication error and isn't retried."""
    fake_panel.inject(Injection("GET", "/logs/", "timeout", delay=1.0))
    started = time.monotonic()
    with pytest.raises(CommunicationError):
        await transport.request("GET", "/logs/", read_timeout=0.3)
    assert time.monotonic() - started < 0.9
    assert len(fake_panel.stats.requests) == 1


async def test_error_responses(fake_panel: FakePanel, transport: Transport) -> None:
    """Error responses are mapped to typed errors."""
    fake_panel.open_zone("209")
    with pytest.raises(ArmingBlockedError) as exc_info:
        await transport.request("PUT", "/system/partitions-1/", {"state": "set"})
    assert [fault.zone_id for fault in exc_info.value.faults] == ["209"]
    fake_panel.installer_locked = True
    with pytest.raises(InstallerLockedError):
        await transport.request("GET", "/system/")


async def test_certificate_verification(fake_panel: FakePanel) -> None:
    """With verification on, the self-signed certificate is rejected."""
    transport = Transport(
        fake_panel.url, fake_panel.user_code, fake_panel.password, verify_ssl=True
    )
    try:
        with pytest.raises(CommunicationError):
            await transport.request("GET", "/system/")
    finally:
        await transport.close()
    assert fake_panel.stats.requests == []


async def test_base_path(fake_panel: FakePanel) -> None:
    """The address may carry a path, e.g. behind a reverse proxy."""
    transport = Transport(
        fake_panel.url + "/panel/", fake_panel.user_code, fake_panel.password
    )
    try:
        # the fake panel has no proxy in front of it
        with pytest.raises(NotFoundError):
            await transport.request("GET", "/system/")
    finally:
        await transport.close()
    assert fake_panel.stats.requests == [("GET", "/panel/system/")]
    fake_panel.violations.clear()


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [(None, "ha-secvest"), ("", "ha-secvest"), ("Custom/1.0", "Custom/1.0")],
)
async def test_user_agent(
    fake_panel: FakePanel, user_agent: str | None, expected: str
) -> None:
    """A User-Agent is always sent; an empty override means the default."""
    transport = (
        Transport(fake_panel.url, fake_panel.user_code, fake_panel.password)
        if user_agent is None
        else Transport(
            fake_panel.url,
            fake_panel.user_code,
            fake_panel.password,
            user_agent=user_agent,
        )
    )
    try:
        await transport.request("GET", "/system/")
    finally:
        await transport.close()
    assert fake_panel.stats.user_agents == [expected]


async def test_no_request_after_a_401(fake_panel: FakePanel) -> None:
    """After a 401 nothing is sent with these credentials again (#6)."""
    transport = Transport(fake_panel.url, fake_panel.user_code, "wrong")
    try:
        results = await asyncio.gather(
            *(transport.request("GET", "/system/") for _ in range(3)),
            return_exceptions=True,
        )
        with pytest.raises(AuthenticationError):
            await transport.request("PUT", "/system/partitions-1/", {"state": "set"})
    finally:
        await transport.close()
    assert all(isinstance(result, AuthenticationError) for result in results)
    assert transport.authentication_failed
    # only the first request reached the panel; the fake panel would also
    # have recorded a violation for repeated credentials
    assert fake_panel.stats.requests == [("GET", "/system/")]


async def test_new_credentials_mean_a_new_transport(fake_panel: FakePanel) -> None:
    """A transport with the corrected credentials works again."""
    failed = Transport(fake_panel.url, fake_panel.user_code, "wrong")
    try:
        with pytest.raises(AuthenticationError):
            await failed.request("GET", "/system/")
    finally:
        await failed.close()
    transport = Transport(fake_panel.url, fake_panel.user_code, fake_panel.password)
    try:
        assert await transport.request("GET", "/system/")
    finally:
        await transport.close()
    assert not transport.authentication_failed


def test_credentials_not_in_errors_or_logs(
    fake_panel: FakePanel, caplog: pytest.LogCaptureFixture
) -> None:
    """Credentials never show up in errors or logs (#50)."""
    caplog.set_level(logging.DEBUG)
    secret = "s3cr3t-pw"
    transport = Transport(fake_panel.url, "9876", secret)
    token = basic_auth("9876", secret).removeprefix("Basic ")

    async def run() -> AuthenticationError:
        try:
            await transport.request("GET", "/system/")
        except AuthenticationError as err:
            return err
        finally:
            await transport.close()
        raise AssertionError

    error = asyncio.run(run())
    texts = [str(error), repr(error), caplog.text]
    assert not any(secret in text or "9876" in text or token in text for text in texts)


def test_address_must_be_https() -> None:
    """Only https addresses are accepted."""
    with pytest.raises(ValueError, match="https"):
        Transport("http://panel", "1", "x")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/faults/", None),
        ("PUT", "/system/partitions-1/", {"state": "partset"}),
    ],
)
async def test_closed_connection_not_noticed_in_time(
    fake_panel: FakePanel,
    transport: Transport,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: dict[str, str] | None,
) -> None:
    """A request into a connection the panel closed goes out once, anew.

    The panel may close the connection just as a request starts; since the
    request didn't go out, it is sent on a new connection, even a command.
    """
    fake_panel.idle_timeout = 0.2
    monkeypatch.setattr(transport_module, "MAX_IDLE", 60)
    await transport.request("GET", "/system/")
    await asyncio.sleep(0.5)
    # the check before sending misses the close
    monkeypatch.setattr(transport_module, "_closed", lambda conn: False)
    await transport.request(method, path, body)
    assert fake_panel.stats.requests == [("GET", "/system/"), (method, path)]
    assert transport.stats.reconnects == 1


async def test_command_lost_after_sending_it_anew(
    fake_panel: FakePanel, transport: Transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command that broke off again after going out anew is a lost connection.

    The first attempt didn't go out (the panel had closed the connection);
    the second went out and its answer was lost, so the outcome is unknown.
    """
    fake_panel.idle_timeout = 0.2
    monkeypatch.setattr(transport_module, "MAX_IDLE", 60)
    await transport.request("GET", "/system/")
    await asyncio.sleep(0.5)
    monkeypatch.setattr(transport_module, "_closed", lambda conn: False)
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "drop_after"))
    with pytest.raises(ConnectionLostError):
        await transport.request("PUT", "/system/partitions-1/", {"state": "set"})
    assert fake_panel.stats.requests == [
        ("GET", "/system/"),
        ("PUT", "/system/partitions-1/"),
    ]
    assert fake_panel.partitions[1].state == "set"


async def test_request_after_close(fake_panel: FakePanel, transport: Transport) -> None:
    """A late request after closing is a communication error; nothing is sent."""
    await transport.close()
    with pytest.raises(CommunicationError):
        await transport.request("GET", "/system/")
    # closing twice is harmless
    await transport.close()
    assert fake_panel.stats.requests == []
