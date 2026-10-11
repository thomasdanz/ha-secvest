"""Reading the wireless zones' status from the panel's web interface (#56).

One read is four requests through the transport's queue: login, the zone
list's data, Info → Zentrale (#194), logout. Never the main page or its event
requests, which make the panel run commands such as a firmware check.
"""

import logging

from ..api.errors import CommunicationError, ConnectionLostError
from ..api.transport import Transport
from .errors import WebCredentialsError, WebLoginFailedError, WebSessionEndedError
from .models import PanelInfo, WebRead
from .parsing import panel_info, session_id, zone_statuses

_LOGGER = logging.getLogger(__name__)

# the web interface's login form takes at most 16 characters per field
MAX_LENGTH = 16

# the form the web interface sends with its lists; bus 0 is the wireless bus.
# The CSRF token isn't needed for the zone list (tested), so it isn't read.
ZONE_LIST_FORM = {
    "bus": "0",
    "busdevice": "0",
    "edit": "0",
    "editSecondary": "0",
    "panelOutputsArePlugby": "0",
    "listingType": "busDevice",
    "listingIOMod": "0",
    "table_start_index": "0",
    "table_range": "10",
    "contact_index": "0",
}


# Info → Zentrale was requested with the overview's form (bus 1)
ABOUT_FORM = {**ZONE_LIST_FORM, "bus": "1"}


def credentials_problem(user_code: str, password: str) -> str | None:
    """Return why the web interface can't log in with these, or None."""
    if "&" in password:
        return "ampersand"
    if len(password) > MAX_LENGTH or len(user_code) > MAX_LENGTH:
        return "too_long"
    return None


class WebClient:
    """Reads the web interface with the REST API's user."""

    def __init__(self, transport: Transport, user_code: str, password: str) -> None:
        """Use the panel's transport; nothing is sent yet."""
        self.transport = transport
        self._user_code = user_code
        self._password = password

    async def read(self) -> WebRead:
        """Log in, read the wireless zone list and Info → Zentrale, log out.

        The whole sequence holds the queue, so no other request comes in
        between. Raises WebLoginFailedError if the login failed or its outcome
        is unknown; the caller must not log in again on its own. Info →
        Zentrale (#194) only adds firmware and serial number: if it fails, the
        read still counts.
        """
        problem = credentials_problem(self._user_code, self._password)
        if problem is not None:
            raise WebCredentialsError(problem)
        async with self.transport.hold():
            ssid = await self._login()
            try:
                status, body = await self.transport.request_web(
                    "POST",
                    "/sec_zones.cgx",
                    ZONE_LIST_FORM,
                    cookie=f"ssid={ssid}",
                    xhr=True,
                )
                if status == 401:
                    raise WebSessionEndedError("the panel ended the web session")
                if status != 200:
                    raise CommunicationError(f"unexpected response status {status}")
                zones, unknown = zone_statuses(body)
                return WebRead(zones, unknown, await self._panel_info(ssid))
            finally:
                await self._logout(ssid)

    async def _login(self) -> str:
        try:
            status, page = await self.transport.request_web(
                "POST",
                "/sec_login.cgi",
                {"usr": self._user_code, "pwd": self._password},
            )
        except ConnectionLostError as err:
            # the login went out, but whether it counted is unknown
            raise WebLoginFailedError("no answer to the web login") from err
        ssid = session_id(page) if status == 200 else None
        if ssid is None:
            raise WebLoginFailedError(f"the web login failed (status {status})")
        return ssid

    async def _panel_info(self, ssid: str) -> PanelInfo | None:
        """Read Info → Zentrale in the same session; None if that fails."""
        try:
            status, page = await self.transport.request_web(
                "POST",
                "/sec_about_panel.cgi",
                ABOUT_FORM,
                cookie=f"ssid={ssid}",
                xhr=True,
            )
        except CommunicationError as err:
            _LOGGER.debug("Reading Info → Zentrale failed: %s", err)
            return None
        if status != 200:
            _LOGGER.debug("Info → Zentrale answered %s", status)
            return None
        return panel_info(page)

    async def _logout(self, ssid: str) -> None:
        """Log out once; the answer doesn't matter and nothing is repeated.

        A late logout, after the panel ended the session, is harmless.
        """
        try:
            await self.transport.request_web(
                "POST",
                "/index.htm",
                {"ssid": "", "logout": "logout"},
                cookie=f"ssid={ssid}",
            )
        except CommunicationError as err:
            _LOGGER.debug("Web logout failed; the panel ends the session: %s", err)
