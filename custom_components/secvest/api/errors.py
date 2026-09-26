"""Errors raised by the API client."""


class SecvestError(Exception):
    """Base class for all errors of the API client."""


class CommunicationError(SecvestError):
    """The panel could not be reached or sent an unexpected response."""
