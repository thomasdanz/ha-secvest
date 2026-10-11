"""Errors of the web interface client."""

from ..api.errors import SecvestError


class WebCredentialsError(SecvestError):
    """The credentials can't log in at the web interface; nothing was sent.

    The web interface's login fails for a password with "&" (the form is
    decoded before it is split) and takes at most 16 characters, while the
    REST API accepts both.
    """


class WebLoginFailedError(SecvestError):
    """The web login failed, or its outcome is unknown.

    Failed web logins count towards a code tamper alarm, so the caller must
    never log in again with these credentials on its own.
    """


class WebSessionEndedError(SecvestError):
    """The panel ended the session (401) before the data request.

    Not wrong credentials: the panel ends idle sessions after about a minute.
    """
