"""Polling rounds and the panel state the entities read."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import Client
from .api.errors import AuthenticationError, InstallerLockedError, SecvestError
from .api.models import PanelEvent, Partition, Zone
from .const import (
    BACKOFF_MAX,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    PAUSE,
    PAUSE_AFTER,
)

if TYPE_CHECKING:
    from . import SecvestConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PanelState:
    """The result of one polling round; replaced as a whole, never changed."""

    partitions: Mapping[int, Partition]
    alarms: tuple[PanelEvent, ...]
    faults: tuple[PanelEvent, ...]
    # the zones of the selected partitions; a zone in several of them once
    zones: Mapping[str, Zone]


def scan_interval(options: Mapping[str, object]) -> timedelta:
    """Return the configured status interval, never below the minimum."""
    value = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    seconds = value if isinstance(value, int | float) else DEFAULT_SCAN_INTERVAL
    if seconds < MIN_SCAN_INTERVAL:
        _LOGGER.warning(
            "Status interval %s s is below the minimum, using %s s",
            seconds,
            MIN_SCAN_INTERVAL,
        )
        seconds = MIN_SCAN_INTERVAL
    return timedelta(seconds=seconds)


@dataclass(slots=True)
class Backoff:
    """Consecutive failed rounds and the delay they cause.

    The panel may need a power cycle when overloaded, so failures never make
    the integration poll harder: each failure doubles the delay up to
    BACKOFF_MAX, and after PAUSE_AFTER failures in a row polling pauses.
    """

    failures: int = 0
    last_error: str | None = None
    # monotonic time before which no round may start
    not_before: float = 0.0

    @property
    def paused(self) -> bool:
        """Return whether polling is paused."""
        return self.failures >= PAUSE_AFTER

    def failed(self, error: Exception, interval: float) -> float:
        """Count a failed round and return the delay before the next one."""
        self.failures += 1
        self.last_error = f"{type(error).__name__}: {error}"
        if self.paused:
            delay = float(PAUSE)
        else:
            delay = float(min(interval * 2**self.failures, BACKOFF_MAX))
        self.not_before = time.monotonic() + delay
        return delay

    def succeeded(self) -> None:
        """Reset after a successful round."""
        self.failures = 0
        self.last_error = None
        self.not_before = 0.0

    def as_dict(self) -> dict[str, object]:
        """Return the state for diagnostics (#13, #43)."""
        return {
            "consecutive_failures": self.failures,
            "paused": self.paused,
            "last_error": self.last_error,
        }


def _round_starts(hass: HomeAssistant) -> dict[str, float]:
    # kept outside the coordinator, so that reloads and setup retries (which
    # create a new coordinator) keep the spacing too
    starts: dict[str, float] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "round_starts", {}
    )
    return starts


class SecvestCoordinator(DataUpdateCoordinator[PanelState]):
    """The only user of the client: runs the polling rounds."""

    config_entry: SecvestConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SecvestConfigEntry, client: Client
    ) -> None:
        """Poll the given panel at the configured interval."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=scan_interval(entry.options),
        )
        self.client = client
        self.selected_partitions: tuple[int, ...] = tuple(
            entry.options.get(CONF_PARTITIONS, ())
        )
        self._missing_reported: set[int] = set()
        self.backoff = Backoff()

    @property
    def available(self) -> bool:
        """Return whether entities are available.

        Single failed rounds keep the last state; entities become unavailable
        only once polling pauses, so they don't flap.
        """
        return self.data is not None and not self.backoff.paused

    async def _async_update_data(self) -> PanelState:
        """Run one round, never sooner than the minimum after the last one."""
        # a manual refresh doesn't shorten the backoff or the pause; it only
        # keeps the round that is already scheduled
        if (remaining := self.backoff.not_before - time.monotonic()) > 0:
            raise UpdateFailed("waiting after failed rounds", retry_after=remaining)
        starts = _round_starts(self.hass)
        entry_id = self.config_entry.entry_id
        if (last := starts.get(entry_id)) is not None:
            wait = last + MIN_SCAN_INTERVAL - time.monotonic()
            if wait > 0:
                _LOGGER.debug("Delaying the round by %.1f s", wait)
                await asyncio.sleep(wait)
        starts[entry_id] = time.monotonic()
        try:
            state = await self._round()
        except AuthenticationError as err:
            # never retried; the transport already blocks further requests
            raise ConfigEntryAuthFailed(str(err)) from err
        except InstallerLockedError as err:
            # the panel answers; the lock doesn't change the interval (#21)
            raise UpdateFailed(str(err)) from err
        except SecvestError as err:
            # timeouts, lost connections, server errors and answers that
            # don't fit the specification
            interval = (self.update_interval or scan_interval({})).total_seconds()
            delay = self.backoff.failed(err, interval)
            if self.backoff.failures == PAUSE_AFTER:
                _LOGGER.warning(
                    "%s failed rounds in a row, pausing polling for %s s: %s",
                    PAUSE_AFTER,
                    PAUSE,
                    err,
                )
            raise UpdateFailed(str(err), retry_after=delay) from err
        self.backoff.succeeded()
        return state

    async def _round(self) -> PanelState:
        """Fetch everything one after another, with the client's operations.

        The queue isn't held, so a command can go ahead between two reads.
        """
        partitions = {p.number: p for p in await self.client.get_partitions()}
        alarms = await self.client.get_alarms()
        faults = await self.client.get_faults()
        zones: dict[str, Zone] = {}
        for number in self.selected_partitions:
            if number not in partitions:
                if number not in self._missing_reported:
                    self._missing_reported.add(number)
                    _LOGGER.warning("Selected partition %s doesn't exist", number)
                continue
            for zone in await self.client.get_zones(number):
                zones.setdefault(zone.id, zone)
        return PanelState(
            partitions=MappingProxyType(partitions),
            alarms=tuple(alarms),
            faults=tuple(faults),
            zones=MappingProxyType(zones),
        )
