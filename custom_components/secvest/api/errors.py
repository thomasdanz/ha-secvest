"""Errors raised by the API client."""

from .models import PanelEvent


class SecvestError(Exception):
    """Base class for all errors of the API client."""


class CommunicationError(SecvestError):
    """The panel could not be reached or sent an unexpected response."""


class ConnectionLostError(CommunicationError):
    """The connection broke after a command was sent.

    The panel may or may not have received it, so the caller has to read the
    real state before deciding anything (see the architecture).
    """


class AuthenticationError(SecvestError):
    """The panel rejected the credentials (401); never retry."""


class InstallerLockedError(SecvestError):
    """The installer is logged in at the panel, which locks the API (403)."""


class NotAllowedError(SecvestError):
    """The panel refused the action with an empty 403.

    The zone isn't omittable, the user may not operate the partition, or the
    method isn't supported; the response doesn't tell which.
    """


class NotFoundError(SecvestError):
    """Unknown path, partition or zone (404)."""


class InvalidRequestError(SecvestError):
    """The panel rejected a value in the request body (400)."""


class ArmingBlockedError(SecvestError):
    """Arming was refused (409); carries the blocking faults.

    The list is empty for a partition without zones.
    """

    def __init__(self, faults: tuple[PanelEvent, ...]) -> None:
        """Keep the blocking faults from the response."""
        super().__init__(f"arming blocked by {len(faults)} fault(s)")
        self.faults = faults
