"""Polling rounds and the panel state the entities read."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import Client
from .api.errors import AuthenticationError, InstallerLockedError, SecvestError
from .api.models import FaultType, PanelEvent, Partition, Zone, ZoneState
from .const import (
    BACKOFF_MAX,
    CONF_AUTH_FAILED,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    PAUSE,
    PAUSE_AFTER,
)
from .groups import zone_groups

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

    @property
    def problems(self) -> tuple[PanelEvent, ...]:
        """Return the faults except "zone open".

        The panel lists every open omittable zone as a fault, even when
        disarmed; those are counted as open zones instead.
        """
        return tuple(f for f in self.faults if f.type != FaultType.ZONE_OPEN)

    def open_zones(self, number: int) -> list[Zone]:
        """Return the partition's zones that are open and not omitted."""
        partition = self.partitions.get(number)
        if partition is None:
            return []
        return [
            zone
            for zone_id in partition.zone_ids
            if (zone := self.zones.get(zone_id)) is not None
            and zone.state == ZoneState.OPEN
            and not zone.omitted
        ]

    def blocking_problems(self, number: int) -> list[PanelEvent]:
        """Return the faults other than open zones that prevent arming."""
        return [f for f in self.problems if f.prevents_set and number in f.partitions]


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


def _partition_issue_id(entry_id: str, number: int) -> str:
    return f"partition_{entry_id}_{number}"


def _group_issue_id(entry_id: str, subentry_id: str) -> str:
    return f"group_{entry_id}_{subentry_id}"


def clear_issues(hass: HomeAssistant, entry_id: str, keep: Iterable[str]) -> None:
    """Delete the repair issues of an entry, except the ids in keep."""
    keep_ids = set(keep)
    prefixes = (
        f"partition_{entry_id}_",
        f"group_{entry_id}_",
        # 0.1.x named the issue for a missing partition differently
        f"missing_partition_{entry_id}_",
    )
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(prefixes)
            and issue_id not in keep_ids
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


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
        # selected partitions that are empty or missing, with the issue raised
        self._partition_problems: dict[int, str] = {}
        # zone groups with zones the partitions no longer list
        self._group_problems: dict[str, tuple[str, ...]] = {}
        self.backoff = Backoff()
        # the installer is logged in at the panel, which locks the API (#21)
        self.installer_locked = False
        # set by setup once the panel device is registered
        self.panel_device_id = ""

    @property
    def available(self) -> bool:
        """Return whether entities are available.

        Single failed rounds keep the last state; entities become unavailable
        only once polling pauses, so they don't flap, or once the panel
        rejected the credentials.
        """
        return (
            self.data is not None
            and not self.backoff.paused
            and not self.client.transport.authentication_failed
        )

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
            # never retried: the transport already blocks further requests,
            # and the flag keeps setup from sending the credentials again
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_AUTH_FAILED: True},
            )
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except InstallerLockedError as err:
            # the panel answers, so no backoff and the interval stays; the
            # round stopped at its first request, and the last state is kept
            if not self.installer_locked:
                _LOGGER.info("The installer is logged in; the panel is locked")
                self.installer_locked = True
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
        self._check_groups(state)
        if self.installer_locked:
            _LOGGER.info("The installer logged out; the panel is unlocked")
            self.installer_locked = False
        return state

    def clear_stale_issues(self) -> None:
        """Delete issues of partitions no longer selected or groups deleted."""
        entry_id = self.config_entry.entry_id
        clear_issues(
            self.hass,
            entry_id,
            keep=[_partition_issue_id(entry_id, n) for n in self._partition_problems]
            + [_group_issue_id(entry_id, s) for s in self._group_problems],
        )

    def listed_zone_ids(self, state: PanelState | None = None) -> set[str]:
        """Return the zones the selected partitions list; others are gone.

        The partitions' zone lists decide, not the zones read, so a zone the
        panel briefly doesn't report still counts.
        """
        state = state or self.data
        return {
            zone_id
            for number in self.selected_partitions
            if (partition := state.partitions.get(number)) is not None
            for zone_id in partition.zone_ids
        }

    def _check_groups(self, state: PanelState) -> None:
        """Raise or clear the repair issue for groups with zones gone."""
        listed = self.listed_zone_ids(state)
        entry = self.config_entry
        for group in zone_groups(entry):
            gone = tuple(z for z in group.zone_ids if z not in listed)
            if gone == self._group_problems.get(group.subentry_id, ()):
                continue
            issue_id = _group_issue_id(entry.entry_id, group.subentry_id)
            if not gone:
                del self._group_problems[group.subentry_id]
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue
            self._group_problems[group.subentry_id] = gone
            _LOGGER.warning(
                "Zone group %s has zones the panel no longer lists: %s",
                group.name,
                ", ".join(gone),
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="zone_group_zones_gone",
                translation_placeholders={
                    "group": group.name,
                    "zones": ", ".join(gone),
                    "name": entry.title,
                },
            )

    def _check_partition(self, number: int, partition: Partition | None) -> None:
        """Raise or clear the repair issue for a selected partition.

        An empty partition (no zones) shows nothing but its state and can't
        be armed; a missing one isn't expected on the tested panel, which
        always reports its four partitions. Both lead to the options.
        """
        if partition is None:
            problem: str | None = "missing_partition"
        elif not partition.zone_ids:
            problem = "empty_partition"
        else:
            problem = None
        if problem == self._partition_problems.get(number):
            return
        issue_id = _partition_issue_id(self.config_entry.entry_id, number)
        if problem is None:
            del self._partition_problems[number]
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        self._partition_problems[number] = problem
        _LOGGER.warning("Selected partition %s: %s", number, problem)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=problem,
            translation_placeholders={
                "partition": str(number)
                if partition is None
                else f"{number} ({partition.name})",
                "name": self.config_entry.title,
            },
        )

    async def _round(self) -> PanelState:
        """Fetch everything one after another, with the client's operations.

        The queue isn't held, so a command can go ahead between two reads.
        """
        partitions = {p.number: p for p in await self.client.get_partitions()}
        alarms = await self.client.get_alarms()
        faults = await self.client.get_faults()
        zones: dict[str, Zone] = {}
        for number in self.selected_partitions:
            partition = partitions.get(number)
            self._check_partition(number, partition)
            if partition is None or not partition.zone_ids:
                # nothing to read
                continue
            for zone in await self.client.get_zones(number):
                zones.setdefault(zone.id, zone)
        return PanelState(
            partitions=MappingProxyType(partitions),
            alarms=tuple(alarms),
            faults=tuple(faults),
            zones=MappingProxyType(zones),
        )
