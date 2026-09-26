"""HTTP handling for the panel.

Maps the panel's responses and connection problems to results and typed
errors. The connection itself (ADR 0001) follows with #2.
"""

from base64 import b64encode
from collections.abc import Iterator
from contextlib import contextmanager
import http.client
import logging
from typing import Any

from .errors import (
    ArmingBlockedError,
    AuthenticationError,
    CommunicationError,
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
