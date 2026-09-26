"""Tests for mapping the panel's responses to results and errors (#4)."""

import http.client
import logging
import ssl

import pytest

from custom_components.secvest.api.errors import (
    ArmingBlockedError,
    AuthenticationError,
    CommunicationError,
    InstallerLockedError,
    InvalidRequestError,
    NotAllowedError,
    NotFoundError,
    SecvestError,
)
from custom_components.secvest.api.models import FaultType
from custom_components.secvest.api.transport import (
    basic_auth,
    check_response,
    translate_errors,
)

from .conftest import FIXTURES, load_fixture


def _fixtures(pattern: str) -> list[str]:
    names = sorted(
        path.name
        for path in FIXTURES.glob(pattern)
        if not path.name.endswith(".request.json")
    )
    assert names, pattern
    return names


@pytest.mark.parametrize(
    "name",
    [
        name
        for name in _fixtures("*.json")
        if not any(f".{status}." in name for status in (400, 403, 409))
    ],
)
def test_ok(name: str) -> None:
    """200 returns the decoded body."""
    assert check_response(200, load_fixture(name)) is not None


def test_ok_with_invalid_body() -> None:
    """A 200 that isn't JSON is a communication error."""
    with pytest.raises(CommunicationError):
        check_response(200, b"")


@pytest.mark.parametrize("name", _fixtures("*.400.html"))
def test_bad_request(name: str) -> None:
    """400 with an HTML body is an invalid request."""
    with pytest.raises(InvalidRequestError):
        check_response(400, load_fixture(name))


@pytest.mark.parametrize("name", _fixtures("*.401.html"))
def test_unauthorized(name: str) -> None:
    """401 with an HTML body is an authentication error."""
    with pytest.raises(AuthenticationError):
        check_response(401, load_fixture(name))


@pytest.mark.parametrize("name", _fixtures("*.403.json"))
def test_installer_locked(name: str) -> None:
    """403 with {"state":"installer"} is the installer lock."""
    with pytest.raises(InstallerLockedError):
        check_response(403, load_fixture(name))


@pytest.mark.parametrize("body", [b"", b"\r\n", b"  "])
def test_not_allowed(body: bytes) -> None:
    """403 with an empty body means the action isn't allowed."""
    with pytest.raises(NotAllowedError):
        check_response(403, body)


@pytest.mark.parametrize("body", [b'{"state":"other"}', b"[]", b"<html></html>"])
def test_unexpected_forbidden(body: bytes) -> None:
    """Any other 403 body is unexpected."""
    with pytest.raises(CommunicationError):
        check_response(403, body)


@pytest.mark.parametrize("name", _fixtures("*.404.html"))
def test_not_found(name: str) -> None:
    """404 with an HTML body is not found."""
    with pytest.raises(NotFoundError):
        check_response(404, load_fixture(name))


@pytest.mark.parametrize("name", _fixtures("*.409*.json"))
def test_arming_blocked(name: str) -> None:
    """409 carries the blocking faults."""
    with pytest.raises(ArmingBlockedError) as exc_info:
        check_response(409, load_fixture(name))
    faults = exc_info.value.faults
    assert all(fault.prevents_set for fault in faults)
    assert all(fault.type is FaultType.ZONE_OPEN for fault in faults)


def test_arming_blocked_without_zones() -> None:
    """A partition without zones gives an empty fault list."""
    with pytest.raises(ArmingBlockedError) as exc_info:
        check_response(409, load_fixture("PUT_system_partitions-2.409.json"))
    assert exc_info.value.faults == ()


def test_arming_blocked_with_unreadable_body(caplog: pytest.LogCaptureFixture) -> None:
    """A 409 is a refused arming even if its body can't be read."""
    caplog.set_level(logging.WARNING)
    with pytest.raises(ArmingBlockedError) as exc_info:
        check_response(409, b"<html></html>")
    assert exc_info.value.faults == ()
    assert len(caplog.records) == 1


@pytest.mark.parametrize("status", [201, 204, 302, 405, 500, 503])
def test_unexpected_status(status: int) -> None:
    """Statuses the panel isn't known to send are communication errors."""
    with pytest.raises(CommunicationError):
        check_response(status, b"")


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionResetError(),
        ConnectionRefusedError(),
        ssl.SSLError("handshake failed"),
        http.client.RemoteDisconnected("closed"),
        http.client.BadStatusLine("x"),
    ],
)
def test_connection_errors(error: Exception) -> None:
    """Timeouts and connection problems become communication errors."""
    with pytest.raises(CommunicationError) as exc_info, translate_errors():
        raise error
    assert exc_info.value.__cause__ is error


def test_other_errors_pass_through() -> None:
    """Errors that aren't connection problems are not translated."""
    with pytest.raises(ValueError, match="bug"), translate_errors():
        raise ValueError("bug")


def test_all_errors_share_a_base_class() -> None:
    """Callers can catch every error of the client at once."""
    for error in (
        ArmingBlockedError(()),
        AuthenticationError(),
        CommunicationError(),
        InstallerLockedError(),
        InvalidRequestError(),
        NotAllowedError(),
        NotFoundError(),
    ):
        assert isinstance(error, SecvestError)


def test_basic_auth() -> None:
    """The header is built for preemptive Basic Auth."""
    assert basic_auth("1234", "pässword") == "Basic MTIzNDpww6Rzc3dvcmQ="
