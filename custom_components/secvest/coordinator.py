"""Polling rounds and the panel state the entities read."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import Client
from .api.errors import (
    AuthenticationError,
    ConnectionLostError,
    InstallerLockedError,
    SecvestError,
)
from .api.models import FaultType, LogEntry, PanelEvent, Partition, Zone, ZoneState
from .const import (
    BACKOFF_MAX,
    CONF_AUTH_FAILED,
    CONF_LOG_INTERVAL,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    DEFAULT_LOG_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FIRST_LOG_DELAY,
    MIN_LOG_INTERVAL,
    MIN_SCAN_INTERVAL,
    PAUSE,
    PAUSE_AFTER,
    UNAVAILABLE_AFTER,
)
from .groups import zone_groups
from .log import LogTracker, log_store

if TYPE_CHECKING:
    from . import SecvestConfigEntry

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """What a command achieved, judged by the state read after it."""

    reached: bool
    # the panel's answer to the (last) command, if it was an error
    error: SecvestError | None
    state: PanelState
    # sent a second time after the connection broke
    resent: bool


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

    def partition_name(self, number: int) -> str:
        """Return the partition's name, or its number if it isn't reported."""
        partition = self.partitions.get(number)
        return partition.name if partition is not None else str(number)


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

    More load than the official app's was never tested on the panel, so
    failures never make the integration poll harder: each failure doubles
    the delay up to BACKOFF_MAX, and after PAUSE_AFTER failures in a row
    polling pauses.
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


def log_interval(options: Mapping[str, object]) -> float:
    """Return the configured log interval in seconds, never below the minimum."""
    value = options.get(CONF_LOG_INTERVAL, DEFAULT_LOG_INTERVAL)
    seconds = value if isinstance(value, int | float) else DEFAULT_LOG_INTERVAL
    return float(max(seconds, MIN_LOG_INTERVAL))


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


@dataclass(slots=True)
class _LastRound:
    """An entry's last successful round, for a reload shortly after it.

    Lock and backoff are kept up to date by later rounds too, so a reload
    after a locked or failed round goes on from there (#145).
    """

    partitions: tuple[int, ...]
    state: PanelState
    installer_locked: bool
    # the coordinator's own, so later failures count here too
    backoff: Backoff


def _last_rounds(hass: HomeAssistant) -> dict[str, _LastRound]:
    rounds: dict[str, _LastRound] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "last_rounds", {}
    )
    return rounds


def forget_rounds(hass: HomeAssistant, entry_id: str) -> None:
    """Forget a removed entry's rounds."""
    _round_starts(hass).pop(entry_id, None)
    _last_rounds(hass).pop(entry_id, None)


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
        self._alarms_failed = False
        # counts commands, so that a round that read before one is discarded
        self._generation = 0
        # zone groups with zones the partitions no longer list
        self._group_problems: dict[str, tuple[str, ...]] = {}
        self.backoff = Backoff()
        # the installer is logged in at the panel, which locks the API (#21)
        self.installer_locked = False
        # set by setup once the panel device is registered
        self.panel_device_id = ""
        # the availability the entities last showed
        self._shown_available = True
        # the log, read with a regular round once its interval is due (#11)
        self.log = LogTracker()
        self._log_store = log_store(hass, entry.entry_id)
        self._log_interval = log_interval(entry.options)
        self._log_due = time.monotonic() + FIRST_LOG_DELAY
        self._log_failed = False
        self._log_listeners: list[Callable[[list[LogEntry]], None]] = []
        # seconds the state reads of the last regular round took (#176)
        self.round_duration: float | None = None
        # notified after every failed round; Home Assistant notifies all
        # entities only at the first of a series (#176)
        self._failure_listeners: list[Callable[[], None]] = []
        # the names of the partitions and zones at setup, by id, so that a
        # rename at the panel is noticed (#137)
        self._names: dict[tuple[str, str], str] | None = None

    @property
    def available(self) -> bool:
        """Return whether entities are available.

        Single failed rounds keep the last state, so entities don't flap;
        from UNAVAILABLE_AFTER failures in a row (about 3 minutes) they are
        unavailable, since a stale state of an alarm panel misleads. Also
        once the panel rejected the credentials.
        """
        return (
            self.data is not None
            and self.backoff.failures < UNAVAILABLE_AFTER
            and not self.client.transport.authentication_failed
        )

    @callback
    def _async_refresh_finished(self) -> None:
        """Let the entities show a change of availability after a failed round.

        Home Assistant notifies the entities only at the first failed round
        of a series, but they become unavailable later (UNAVAILABLE_AFTER, the
        pause); without this they would keep showing the stale state.
        """
        available = self.available
        changed = available != self._shown_available
        self._shown_available = available
        if self.last_update_success:
            return
        if changed:
            self.async_update_listeners()
        elif self.backoff.failures > 1:
            # only the diagnostic sensors, so the failed rounds count up;
            # the others keep their state (#176)
            for listener in list(self._failure_listeners):
                listener()

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
        generation = self._generation
        try:
            # the slow log first, so the state the round publishes is fresh
            await self._poll_log()
            # measured without the log read, which takes about 6 s (#176)
            started = time.monotonic()
            state = await self._round()
            duration = time.monotonic() - started
        except AuthenticationError as err:
            self._remember_auth_failed()
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except InstallerLockedError as err:
            # the panel answers, so no backoff and the interval stays; the
            # round stopped at its first request, and the last state is kept
            self._set_installer_locked()
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
        if generation != self._generation and self.data is not None:
            # a command ran meanwhile and its verification is newer than what
            # this round read before it
            _LOGGER.debug("Discarding a round that a command overtook")
            return self.data
        self._accept(state)
        self.round_duration = duration
        # only in a regular round, so a reload never falls into a command
        self._check_renames(state)
        return state

    def _check_renames(self, state: PanelState) -> None:
        """Reload once a partition or zone was renamed at the panel (#137).

        Matched by partition number and zone id, never by name. The reload
        takes this round's result, so nothing more is sent; it gives the
        entities and zone devices their new names, while names set in Home
        Assistant and all ids stay.
        """
        names = _names(state, self.selected_partitions)
        if self._names is None:
            self._names = names
            return
        renamed = sorted(
            f"{kind} {item}"
            for (kind, item), name in names.items()
            if self._names.get((kind, item), name) != name
        )
        if renamed:
            _LOGGER.info(
                "Renamed at the panel: %s; reloading to show the new names",
                ", ".join(renamed),
            )
            # once: the new coordinator starts from the new names
            self._names = names
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    async def async_read_installation_name(self) -> str:
        """Read the installation's name once, like the app at its start.

        GET /system/ isn't part of a round; the options read it on request
        (#137). A 401 or the installer lock count as in a round.
        """
        try:
            system = await self.client.get_system()
        except (AuthenticationError, InstallerLockedError) as err:
            self.note_panel_error(err)
            raise
        return system.name

    @property
    def log_interval(self) -> float:
        """Return the log interval in seconds."""
        return self._log_interval

    @property
    def log_read_failed(self) -> bool:
        """Return whether the last log read failed."""
        return self._log_failed

    async def async_load_log(self) -> None:
        """Restore the log state of the last run, so nothing is replayed."""
        data = await self._log_store.async_load()
        if data is not None:
            self.log = LogTracker.from_dict(data)

    @callback
    def async_add_failure_listener(
        self, listener: Callable[[], None]
    ) -> Callable[[], None]:
        """Call the listener after every failed round, also the further ones."""
        self._failure_listeners.append(listener)
        return lambda: self._failure_listeners.remove(listener)

    @callback
    def async_add_log_listener(
        self, listener: Callable[[list[LogEntry]], None]
    ) -> Callable[[], None]:
        """Call the listener with each batch of new log entries, oldest first."""
        self._log_listeners.append(listener)
        return lambda: self._log_listeners.remove(listener)

    async def _poll_log(self) -> None:
        """Read the log at the start of a round, if its interval is due.

        Before the round's reads, on the same connection, so it never runs in
        parallel to them and the state the round publishes isn't held back
        by the slow log read. A failure doesn't fail the round: the state is
        read as usual, and the log is read again after the next interval. A
        401 and the installer lock end the round, like any request.
        """
        if time.monotonic() < self._log_due:
            return
        self._log_due = time.monotonic() + self._log_interval
        try:
            if self.log.has_baseline:
                new = self.log.take(await self.client.get_log_since(self.log.since()))
            else:
                # once, as the baseline; it fires nothing
                self.log.baseline(await self.client.get_log())
                new = None
        except AuthenticationError, InstallerLockedError:
            raise
        except SecvestError as err:
            if not self._log_failed:
                self._log_failed = True
                _LOGGER.warning("Reading the log failed, trying again later: %s", err)
            return
        if self._log_failed:
            self._log_failed = False
            _LOGGER.info("Reading the log works again")
        if new == []:
            return
        await self._log_store.async_save(self.log.as_dict())
        if new:
            for listener in list(self._log_listeners):
                listener(new)

    def _accept(self, state: PanelState) -> None:
        """Take a successful round's result into account."""
        self.backoff.succeeded()
        if self.installer_locked:
            _LOGGER.info("The installer logged out; the panel is unlocked")
            self.installer_locked = False
        _last_rounds(self.hass)[self.config_entry.entry_id] = _LastRound(
            self.selected_partitions, state, False, self.backoff
        )
        self._check_issues(state)

    def _check_issues(self, state: PanelState) -> None:
        """Raise or clear the repair issues; only from a complete round."""
        for number in self.selected_partitions:
            self._check_partition(number, state.partitions.get(number))
        self._check_groups(state)

    def _remember_auth_failed(self) -> None:
        # never retried: the transport already blocks further requests, and
        # the flag keeps setup from sending the credentials again
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_AUTH_FAILED: True},
        )

    def _set_installer_locked(self) -> None:
        if not self.installer_locked:
            _LOGGER.info("The installer is logged in; the panel is locked")
            self.installer_locked = True
            if last := _last_rounds(self.hass).get(self.config_entry.entry_id):
                last.installer_locked = True

    def note_panel_error(self, err: SecvestError) -> None:
        """Take in a 401 or the installer lock from a request outside a round.

        As in a round: a 401 is remembered and starts the reauthentication,
        the installer lock is shown. Other errors are left to the caller.
        """
        if isinstance(err, AuthenticationError):
            self._remember_auth_failed()
            self.config_entry.async_start_reauth(self.hass)
        elif isinstance(err, InstallerLockedError):
            self._set_installer_locked()
            self.async_update_listeners()

    async def async_command(
        self,
        send: Callable[[], Awaitable[object]],
        reached: Callable[[PanelState], bool],
        *,
        publish: bool = True,
    ) -> CommandOutcome:
        """Send a command, then read the real state and judge by it.

        The caller holds the request queue (with priority) for the whole
        sequence. The verification runs after any answer, also an error;
        only a 401 and the installer lock end the command without it, since
        every request fails then. If the connection broke after sending
        (ConnectionLostError), the command is sent once more, but only if the
        verification shows the target wasn't reached: the single automatic
        retry of a command (see the architecture).

        Within a sequence of commands, publish=False keeps the entities on
        their previous state until the caller publishes the final one.
        """
        error: SecvestError | None = None
        resent = False
        for attempt in (1, 2):
            try:
                await send()
                error = None
            except (AuthenticationError, InstallerLockedError) as err:
                self.note_panel_error(err)
                raise
            except SecvestError as err:
                error = err
            state = await self._verify(publish=publish)
            if reached(state) or not isinstance(error, ConnectionLostError):
                break
            if attempt == 1:
                _LOGGER.info("The connection broke after a command; sending it again")
                resent = True
        return CommandOutcome(reached(state), error, state, resent)

    async def _verify(self, *, publish: bool = True) -> PanelState:
        """Read the real state right after a command.

        It replaces the next regular round: the schedule starts again, and
        the minimum spacing counts from here. A round that read before the
        command discards its result.
        """
        self._generation += 1
        _round_starts(self.hass)[self.config_entry.entry_id] = time.monotonic()
        try:
            state = await self._round()
        except (AuthenticationError, InstallerLockedError) as err:
            self.note_panel_error(err)
            raise
        self._accept(state)
        if publish:
            self.async_set_updated_data(state)
        return state

    def reuse_recent_round(self) -> bool:
        """Take the last round's result if a new round would have to wait.

        A reload (changed options or zone groups) would otherwise wait up to
        the minimum spacing before its first round. The result is only taken
        for the same selected partitions; nothing is sent. The installer lock
        and the backoff go on from where the rounds since left them.
        """
        entry_id = self.config_entry.entry_id
        start = _round_starts(self.hass).get(entry_id)
        last = _last_rounds(self.hass).get(entry_id)
        if (
            start is None
            or last is None
            or last.partitions != self.selected_partitions
            or time.monotonic() - start >= MIN_SCAN_INTERVAL
        ):
            return False
        self.installer_locked = last.installer_locked
        self.backoff = last.backoff
        self._check_issues(last.state)
        self._names = _names(last.state, self.selected_partitions)
        self.data = last.state
        self.last_update_success = True
        self._shown_available = self.available
        _LOGGER.debug("Reusing the round from %.1f s ago", time.monotonic() - start)
        return True

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

    async def _alarms(self) -> list[PanelEvent]:
        """Read /alarms/; the partition state detects an alarm without it.

        So a failing /alarms/ doesn't fail the round: it only adds details.
        A 401 and the installer lock still end the round, like any request.
        """
        try:
            return await self.client.get_alarms()
        except AuthenticationError, InstallerLockedError:
            raise
        except SecvestError as err:
            if not self._alarms_failed:
                self._alarms_failed = True
                _LOGGER.warning("Reading the alarms failed, going on without: %s", err)
            return []

    async def _round(self) -> PanelState:
        """Fetch everything one after another, with the client's operations.

        The queue isn't held, so a command can go ahead between two reads.
        """
        partitions = {p.number: p for p in await self.client.get_partitions()}
        alarms = await self._alarms()
        faults = await self.client.get_faults()
        zones: dict[str, Zone] = {}
        for number in self.selected_partitions:
            partition = partitions.get(number)
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


def _names(state: PanelState, partitions: Iterable[int]) -> dict[tuple[str, str], str]:
    """Return the names of the selected partitions and of the zones, by id."""
    names = {
        ("partition", str(number)): partition.name
        for number in partitions
        if (partition := state.partitions.get(number)) is not None
    }
    names.update({("zone", zone.id): zone.name for zone in state.zones.values()})
    return names
