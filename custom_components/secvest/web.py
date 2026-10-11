"""Reading the wireless zones' status from the panel's web interface (#56).

Separate from the polling rounds: its own, long interval, its own entities.
Values from the web interface and the REST API are never mixed in one entity,
since they are read at very different intervals. The requests go through the
panel's transport, so there is still one connection and one queue.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.errors import AuthenticationError, SecvestError
from .const import (
    CONF_USER_CODE,
    CONF_WEB_INTERFACE,
    CONF_WEB_INTERVAL,
    CONF_WEB_LOGIN_FAILED,
    DEFAULT_WEB_INTERVAL,
    DOMAIN,
    FIRST_WEB_DELAY,
    MAX_WEB_INTERVAL,
    MIN_WEB_INTERVAL,
    MIN_WEB_SPACING,
    TESTED_FIRMWARE,
)
from .webui.client import WebClient, credentials_problem
from .webui.errors import WebCredentialsError, WebLoginFailedError
from .webui.models import Level, PanelInfo, WebZoneStatus

if TYPE_CHECKING:
    from . import SecvestConfigEntry
    from .coordinator import SecvestCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WebState:
    """The result of one read of the web interface."""

    zones: Mapping[str, WebZoneStatus]
    read_at: datetime
    # firmware and serial number from Info → Zentrale (#194), if read
    panel: PanelInfo | None = None


def web_enabled(options: Mapping[str, object]) -> bool:
    """Return whether the web interface is read."""
    return bool(options.get(CONF_WEB_INTERFACE, False))


def web_interval(options: Mapping[str, object]) -> timedelta:
    """Return the configured interval, within its limits."""
    value = options.get(CONF_WEB_INTERVAL, DEFAULT_WEB_INTERVAL)
    hours = value if isinstance(value, int | float) else DEFAULT_WEB_INTERVAL
    return timedelta(hours=min(max(hours, MIN_WEB_INTERVAL), MAX_WEB_INTERVAL))


def dbm_range(level: int) -> tuple[int | None, int | None]:
    """Return the dBm range of a level: from (inclusive), to (exclusive).

    The installer manual's table for the signal strength test: 0 below
    -101 dBm, then steps of 3 dB, 9 at -77 dBm or more; open ends are None.
    That it applies to the zone list too is assumed (same format).
    """
    if level <= 0:
        return None, -101
    if level >= 9:
        return -77, None
    return -104 + 3 * level, -101 + 3 * level


def level_attributes(level: Level | None) -> dict[str, int | None]:
    """Return the lowest level and the dBm range for an entity's attributes."""
    if level is None:
        return {"lowest_level": None, "dbm_from": None, "dbm_to": None}
    low, high = dbm_range(level.current)
    return {"lowest_level": level.lowest, "dbm_from": low, "dbm_to": high}


def _login_issue_id(entry_id: str) -> str:
    return f"web_{entry_id}_login_failed"


def _credentials_issue_id(entry_id: str) -> str:
    return f"web_{entry_id}_credentials"


def _firmware_issue_id(entry_id: str) -> str:
    return f"web_{entry_id}_firmware"


def clear_web_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the web interface's repair issues of an entry."""
    ir.async_delete_issue(hass, DOMAIN, _login_issue_id(entry_id))
    ir.async_delete_issue(hass, DOMAIN, _credentials_issue_id(entry_id))
    ir.async_delete_issue(hass, DOMAIN, _firmware_issue_id(entry_id))


@dataclass(slots=True)
class _LastRead:
    state: WebState
    # monotonic time of the read
    at: float


def _last_reads(hass: HomeAssistant) -> dict[str, _LastRead]:
    # kept outside the coordinator, so a reload neither reads again at once
    # nor loses the values
    reads: dict[str, _LastRead] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "web_reads", {}
    )
    return reads


def forget_web_reads(hass: HomeAssistant, entry_id: str) -> None:
    """Forget a removed entry's last read."""
    _last_reads(hass).pop(entry_id, None)


class WebCoordinator(DataUpdateCoordinator[WebState | None]):
    """Reads the web interface every few hours, and on request."""

    config_entry: SecvestConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SecvestConfigEntry,
        panel: SecvestCoordinator,
    ) -> None:
        """Read through the panel's transport, with the entry's panel user."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} web interface",
            update_interval=web_interval(entry.options),
            always_update=False,
        )
        self.panel = panel
        user_code, password = entry.data[CONF_USER_CODE], entry.data[CONF_PASSWORD]
        self.client = WebClient(panel.client.transport, user_code, password)
        self.last_error: str | None = None
        # values the panel sent that couldn't be read, logged once each
        self._unknown: set[str] = set()
        # the untested firmware already logged
        self._firmware_warned: str | None = None
        self.credentials_problem = credentials_problem(user_code, password)
        if self.blocked:
            # nothing to schedule; entities show the web interface unavailable
            self.update_interval = None

    @property
    def login_failed(self) -> bool:
        """Whether a failed web login stopped the reads (until confirmed)."""
        return bool(self.config_entry.data.get(CONF_WEB_LOGIN_FAILED))

    @property
    def blocked(self) -> bool:
        """Whether nothing is sent to the web interface."""
        return self.login_failed or self.credentials_problem is not None

    @property
    def last_read(self) -> float | None:
        """Return the monotonic time of the last read, if any."""
        last = _last_reads(self.hass).get(self.config_entry.entry_id)
        return None if last is None else last.at

    @callback
    def async_start(self) -> Callable[[], None]:
        """Take over the last read and schedule the first one.

        After a reload the last read's values are shown at once and the next
        read keeps the interval; otherwise the first read comes shortly after
        setup, so setup doesn't wait for it. Returns the cancel callback.
        """
        self._check_issues()
        if self.blocked:
            return lambda: None
        entry_id = self.config_entry.entry_id
        delay: float = FIRST_WEB_DELAY
        if (last := _last_reads(self.hass).get(entry_id)) is not None:
            self.data = last.state
            interval = (self.update_interval or web_interval({})).total_seconds()
            delay = max(last.at + interval - time.monotonic(), FIRST_WEB_DELAY)

        async def _read(_now: datetime) -> None:
            await self.async_refresh()

        return async_call_later(self.hass, delay, _read)

    def _check_issues(self) -> None:
        """Raise or clear the repair issues for a blocked web interface."""
        entry = self.config_entry
        if self.login_failed:
            self._login_issue()
        else:
            ir.async_delete_issue(self.hass, DOMAIN, _login_issue_id(entry.entry_id))
        issue_id = _credentials_issue_id(entry.entry_id)
        if self.credentials_problem is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        _LOGGER.warning(
            "The web interface can't log in with this password (%s); it isn't read",
            self.credentials_problem,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="web_credentials",
            translation_placeholders={"name": entry.title},
        )

    def _login_issue(self) -> None:
        entry = self.config_entry
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            _login_issue_id(entry.entry_id),
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key="web_login_failed",
            translation_placeholders={"name": entry.title},
            data={"entry_id": entry.entry_id},
        )

    async def _async_update_data(self) -> WebState | None:
        """Read the web interface, unless it is blocked or was read just now."""
        if self.blocked:
            raise UpdateFailed("the web interface isn't read")
        last = self.last_read
        if last is not None and time.monotonic() - last < MIN_WEB_SPACING:
            _LOGGER.debug("Web interface read recently, keeping its values")
            return self.data
        if self.panel.installer_locked:
            # the installer's session locks the panel; try at the next interval
            raise UpdateFailed("the installer is logged in")
        try:
            read = await self.client.read()
        except WebCredentialsError as err:  # pragma: no cover - checked before
            raise UpdateFailed(str(err)) from err
        except WebLoginFailedError as err:
            self._remember_login_failed(err)
            raise UpdateFailed(str(err)) from err
        except AuthenticationError as err:
            # the REST API's 401 or an untrusted certificate stopped all
            # requests; the polling rounds handle it
            raise UpdateFailed(str(err)) from err
        except SecvestError as err:
            # the values stay; the next interval tries again
            self.last_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Reading the web interface failed: %s", err)
            raise UpdateFailed(str(err)) from err
        for item in read.unknown:
            if item not in self._unknown:
                self._unknown.add(item)
                _LOGGER.warning("Unknown value from the web interface: %s", item)
        self.last_error = None
        if read.panel is not None:
            self._take_panel_info(read.panel)
        state = WebState(
            zones=MappingProxyType(read.zones),
            read_at=dt_util.utcnow(),
            panel=read.panel,
        )
        _last_reads(self.hass)[self.config_entry.entry_id] = _LastRead(
            state, time.monotonic()
        )
        return state

    def _take_panel_info(self, info: PanelInfo) -> None:
        """Show firmware and serial number on the panel device (#194).

        A firmware other than the tested one (ADR 0005) is logged once and
        raises a repair issue that can be ignored; nothing else changes.
        """
        # a value the page didn't show keeps what the device has
        if self.panel.panel_device_id and (info.firmware or info.serial_number):
            dr.async_get(self.hass).async_update_device(
                self.panel.panel_device_id,
                sw_version=UNDEFINED if info.firmware is None else info.firmware,
                serial_number=(
                    UNDEFINED if info.serial_number is None else info.serial_number
                ),
            )
        issue_id = _firmware_issue_id(self.config_entry.entry_id)
        if info.firmware is None or info.firmware == TESTED_FIRMWARE:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        if info.firmware != self._firmware_warned:
            self._firmware_warned = info.firmware
            _LOGGER.warning(
                "The panel runs firmware %s; tested is %s",
                info.firmware,
                TESTED_FIRMWARE,
            )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="untested_firmware",
            translation_placeholders={
                "name": self.config_entry.title,
                "firmware": info.firmware,
                "tested": TESTED_FIRMWARE,
            },
        )

    def _remember_login_failed(self, err: WebLoginFailedError) -> None:
        """Stop all web reads, also after a restart, and tell the user.

        Failed web logins count towards a code tamper alarm, so the login is
        never tried again on its own.
        """
        self.last_error = f"{type(err).__name__}: {err}"
        _LOGGER.error(
            "The web interface login failed (%s); it isn't read again until "
            "confirmed in the repair issue",
            err,
        )
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_WEB_LOGIN_FAILED: True}
        )
        self._login_issue()
        self.update_interval = None
        self.async_update_listeners()

    def as_dict(self) -> dict[str, object]:
        """Return the state for diagnostics."""
        state = self.data
        return {
            "interval_hours": (self.update_interval or web_interval({})).total_seconds()
            / 3600,
            "login_failed": self.login_failed,
            "credentials_problem": self.credentials_problem,
            "last_read": None if state is None else state.read_at.isoformat(),
            "zones": 0 if state is None else len(state.zones),
            # the serial number is left out: it identifies the installation
            "firmware": None
            if state is None or state.panel is None
            else state.panel.firmware,
            "last_error": self.last_error,
        }
