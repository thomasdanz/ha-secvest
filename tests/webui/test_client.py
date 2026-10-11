"""The web interface client against the fake panel (#56)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from custom_components.secvest.api import transport as transport_module
from custom_components.secvest.api.client import Client
from custom_components.secvest.api.errors import (
    AuthenticationError,
    CommunicationError,
)
from custom_components.secvest.api.transport import Transport
from custom_components.secvest.webui.client import WebClient, credentials_problem
from custom_components.secvest.webui.errors import (
    WebCredentialsError,
    WebLoginFailedError,
    WebSessionEndedError,
)
from custom_components.secvest.webui.models import Level

from ..fake_panel import FakePanel, Injection, WebRow

LOGIN = ("POST", "/sec_login.cgi")
DATA = ("POST", "/sec_zones.cgx")
ABOUT = ("POST", "/sec_about_panel.cgi")
LOGOUT = ("POST", "/index.htm")


@pytest.fixture
async def transport(fake_panel: FakePanel) -> AsyncIterator[Transport]:
    """Return a transport for the fake panel."""
    transport = Transport(fake_panel.url, fake_panel.user_code, fake_panel.password)
    yield transport
    await transport.close()


def _client(
    fake_panel: FakePanel, transport: Transport, password: str | None = None
) -> WebClient:
    return WebClient(transport, fake_panel.user_code, password or fake_panel.password)


async def test_read(fake_panel: FakePanel, transport: Transport) -> None:
    """Login, the zone list, Info → Zentrale, logout; the session ended."""
    fake_panel.web_rows["212"] = WebRow(level=0, lowest=0, repeater=(6, 0))
    fake_panel.web_rows["205"].battery_low = True
    result = await _client(fake_panel, transport).read()
    statuses, unknown = result.zones, result.unknown
    assert unknown == []
    assert len(statuses) == 19
    assert statuses["212"].signal == Level(0, 0)
    assert statuses["212"].repeater_signal == Level(6, 0)
    assert statuses["205"].battery_low is True
    assert result.panel is not None
    assert result.panel.firmware == "v3.01.31"
    assert result.panel.serial_number == "SECVEST0000TEST01"
    assert fake_panel.stats.requests == [LOGIN, DATA, ABOUT, LOGOUT]
    assert fake_panel.web_sessions == {}


async def test_shares_the_connection(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """REST and web requests go over one connection, one after another."""
    await Client(transport).get_partitions()
    await _client(fake_panel, transport).read()
    assert fake_panel.stats.connections == 1
    assert fake_panel.stats.max_open_connections == 1


async def test_failed_login(fake_panel: FakePanel, transport: Transport) -> None:
    """A refused login: no data request, no logout, nothing more."""
    with pytest.raises(WebLoginFailedError):
        await _client(fake_panel, transport, password="wrong").read()
    assert fake_panel.stats.requests == [LOGIN]
    # the REST API isn't blocked: the password works there
    assert not transport.authentication_failed


@pytest.mark.parametrize(
    "injection",
    [
        Injection("POST", "/sec_login.cgi", "status", status=500),
        Injection("POST", "/sec_login.cgi", "status", status=200, body=b"<html/>"),
        Injection("POST", "/sec_login.cgi", "drop_after"),
        Injection("POST", "/sec_login.cgi", "timeout", delay=0.5),
    ],
    ids=["server_error", "no_session_id", "no_answer", "timeout"],
)
async def test_login_without_a_session(
    fake_panel: FakePanel,
    transport: Transport,
    injection: Injection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anything but a session id counts as a failed login; nothing follows."""
    monkeypatch.setattr("custom_components.secvest.api.transport.WEB_READ_TIMEOUT", 0.2)
    fake_panel.inject(injection)
    with pytest.raises(WebLoginFailedError):
        await _client(fake_panel, transport).read()
    assert fake_panel.stats.requests == [LOGIN]


async def test_panel_unreachable(fake_panel: FakePanel, transport: Transport) -> None:
    """Nothing sent is no failed login: a plain communication error."""
    fake_panel.stop()
    with pytest.raises(CommunicationError) as err:
        await _client(fake_panel, transport).read()
    assert not isinstance(err.value, WebLoginFailedError)
    fake_panel.start()


async def test_session_ended(fake_panel: FakePanel, transport: Transport) -> None:
    """A 401 for the data means the session ended; the logout still goes out."""
    fake_panel.web_session_timeout = -1
    with pytest.raises(WebSessionEndedError):
        await _client(fake_panel, transport).read()
    assert fake_panel.stats.requests == [LOGIN, DATA, LOGOUT]
    # an ended web session isn't a REST 401
    assert not transport.authentication_failed


@pytest.mark.parametrize(
    "injection",
    [
        Injection("POST", "/sec_zones.cgx", "status", status=500),
        Injection("POST", "/sec_zones.cgx", "status", status=200, body=b"<html/>"),
        Injection("POST", "/sec_zones.cgx", "drop_after"),
    ],
    ids=["server_error", "not_the_data", "no_answer"],
)
async def test_data_request_fails(
    fake_panel: FakePanel, transport: Transport, injection: Injection
) -> None:
    """A failed data request is a communication error; the logout goes out."""
    fake_panel.inject(injection)
    with pytest.raises(CommunicationError):
        await _client(fake_panel, transport).read()
    assert fake_panel.stats.requests == [LOGIN, DATA, LOGOUT]


@pytest.mark.parametrize(
    "injection",
    [
        Injection("POST", "/index.htm", "status", status=500),
        Injection("POST", "/index.htm", "drop_after"),
    ],
)
async def test_logout_fails(
    fake_panel: FakePanel, transport: Transport, injection: Injection
) -> None:
    """The logout's answer doesn't matter, and it isn't repeated."""
    fake_panel.inject(injection)
    statuses = (await _client(fake_panel, transport).read()).zones
    assert len(statuses) == 19
    assert fake_panel.stats.requests == [LOGIN, DATA, ABOUT, LOGOUT]


async def test_closed_before_sending(
    fake_panel: FakePanel, transport: Transport
) -> None:
    """A connection the panel closed before the login went out is replaced."""
    await Client(transport).get_partitions()
    fake_panel.close_connections()
    statuses = (await _client(fake_panel, transport).read()).zones
    assert len(statuses) == 19


@pytest.mark.parametrize(
    ("password", "user_code", "problem"),
    [
        ("a&b", "1234", "ampersand"),
        ("x" * 17, "1234", "too_long"),
        ("secret", "1" * 17, "too_long"),
    ],
)
async def test_unusable_credentials(
    fake_panel: FakePanel,
    transport: Transport,
    password: str,
    user_code: str,
    problem: str,
) -> None:
    """Credentials the web interface can't take are refused before sending."""
    assert credentials_problem(user_code, password) == problem
    with pytest.raises(WebCredentialsError):
        await WebClient(transport, user_code, password).read()
    assert fake_panel.stats.requests == []


async def test_not_after_a_rest_401(fake_panel: FakePanel) -> None:
    """After the REST API's 401 nothing reaches the web interface either."""
    transport = Transport(fake_panel.url, fake_panel.user_code, "wrong")
    try:
        with pytest.raises(AuthenticationError):
            await Client(transport).get_system()
        sent = len(fake_panel.stats.requests)
        with pytest.raises(AuthenticationError):
            await WebClient(transport, fake_panel.user_code, "wrong").read()
        assert len(fake_panel.stats.requests) == sent
    finally:
        await transport.close()


async def test_not_sent_is_sent_once_more(
    fake_panel: FakePanel, transport: Transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request the panel closed the connection for before it went out."""
    original = transport._exchange
    failures = [True]

    def exchange(*args: Any, **kwargs: Any) -> tuple[int, bytes]:
        if failures:
            failures.pop()
            raise transport_module._NotSentError
        return original(*args, **kwargs)

    monkeypatch.setattr(transport, "_exchange", exchange)
    statuses = (await _client(fake_panel, transport).read()).zones
    assert len(statuses) == 19


async def test_not_sent_twice(
    fake_panel: FakePanel, transport: Transport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not sent twice in a row: a communication error, no failed login."""

    def exchange(*args: Any, **kwargs: Any) -> tuple[int, bytes]:
        raise transport_module._NotSentError

    monkeypatch.setattr(transport, "_exchange", exchange)
    with pytest.raises(CommunicationError) as err:
        await _client(fake_panel, transport).read()
    assert not isinstance(err.value, WebLoginFailedError)


async def test_closed_transport(fake_panel: FakePanel, transport: Transport) -> None:
    """After closing, nothing is sent."""
    await transport.close()
    with pytest.raises(CommunicationError):
        await _client(fake_panel, transport).read()
    assert fake_panel.stats.requests == []


@pytest.mark.parametrize(
    "injection",
    [
        Injection("POST", "/sec_about_panel.cgi", "status", status=500),
        Injection("POST", "/sec_about_panel.cgi", "drop_after"),
    ],
)
async def test_info_page_fails(
    fake_panel: FakePanel, transport: Transport, injection: Injection
) -> None:
    """Info → Zentrale only adds details: the read counts without it."""
    fake_panel.inject(injection)
    result = await _client(fake_panel, transport).read()
    assert len(result.zones) == 19
    assert result.panel is None
    assert fake_panel.stats.requests == [LOGIN, DATA, ABOUT, LOGOUT]
