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
from .api.errors import AuthenticationError, SecvestError
from .api.models import PanelEvent, Partition, Zone
from .const import (
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
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

    async def _async_update_data(self) -> PanelState:
        """Run one round, never sooner than the minimum after the last one."""
        starts = _round_starts(self.hass)
        entry_id = self.config_entry.entry_id
        if (last := starts.get(entry_id)) is not None:
            wait = last + MIN_SCAN_INTERVAL - time.monotonic()
            if wait > 0:
                _LOGGER.debug("Delaying the round by %.1f s", wait)
                await asyncio.sleep(wait)
        starts[entry_id] = time.monotonic()
        try:
            return await self._round()
        except AuthenticationError as err:
            # never retried; the transport already blocks further requests
            raise ConfigEntryAuthFailed(str(err)) from err
        except SecvestError as err:
            raise UpdateFailed(str(err)) from err

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
