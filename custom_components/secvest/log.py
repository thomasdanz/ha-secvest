"""The panel's log, read incrementally without gaps (#11).

The full log (up to 600 entries) is read once, as the baseline; it fires
nothing. Afterwards only the entries from one hour before the newest known
one are read. The overlap returns known entries again on purpose: it covers
entries written later within the same second, and the hour the panel's
local clock repeats when daylight saving time ends. Known entries are
recognised by their whole content, since the id seems to be derived from
the timestamp and can repeat when the clock goes back.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .api.models import LogEntry, LogEvent, LogType
from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# seconds before the newest known entry that are read again
OVERLAP = 3600
_STORE_VERSION = 1


def log_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store that keeps an entry's log state across restarts."""
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.log.{entry_id}")


def _timestamp(entry: LogEntry) -> int | None:
    return entry.events[0].timestamp if entry.events else None


class LogTracker:
    """The newest known log timestamp and the entries of the overlap window."""

    def __init__(self, newest: int | None = None, known: Iterable[LogEntry] = ()):
        """Start without a baseline, or from a stored state."""
        self.newest = newest
        self._known = set(known)
        self._prune()

    @property
    def has_baseline(self) -> bool:
        """Whether the full log was read once (here or before a restart)."""
        return self.newest is not None

    def since(self) -> int:
        """Return the panel timestamp the next read starts at."""
        return max(0, (self.newest or 0) - OVERLAP)

    def baseline(self, entries: Iterable[LogEntry]) -> None:
        """Take the full log as known; none of it is new."""
        entries = list(entries)
        self.newest = max(
            (t for e in entries if (t := _timestamp(e)) is not None), default=0
        )
        self._known = set(entries)
        self._prune()

    def take(self, entries: Iterable[LogEntry]) -> list[LogEntry]:
        """Return the entries not known yet, oldest first, and remember them."""
        new: list[LogEntry] = []
        for entry in entries:
            if entry not in self._known and entry not in new:
                new.append(entry)
        self._known.update(new)
        stamps = [t for e in new if (t := _timestamp(e)) is not None]
        if stamps:
            self.newest = max(self.newest or 0, *stamps)
        self._prune()
        # the panel lists the newest first: turned round, and sorted stably
        # by time, entries of the same second keep the order they were written
        new.reverse()
        return sorted(new, key=lambda e: _timestamp(e) or 0)

    def _prune(self) -> None:
        if self.newest is None:
            return
        oldest = self.since()
        self._known = {
            entry
            for entry in self._known
            if (t := _timestamp(entry)) is not None and t >= oldest
        }

    def as_dict(self) -> dict[str, Any]:
        """Return the state to store."""
        return {
            "newest": self.newest,
            "known": [asdict(entry) for entry in self._known],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LogTracker:
        """Restore a stored state; anything unreadable means a new baseline."""
        try:
            newest = data["newest"]
            known = [_entry(item) for item in data["known"]]
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Stored log state unreadable, reading a new baseline: %s", err
            )
            return cls()
        if newest is not None and not isinstance(newest, int):
            return cls()
        return cls(newest, known)


def _entry(data: Mapping[str, Any]) -> LogEntry:
    log_type = data["type"]
    return LogEntry(
        id=str(data["id"]),
        type=LogType(log_type) if log_type in set(LogType) else str(log_type),
        text=str(data["text"]),
        events=tuple(
            LogEvent(
                timestamp=int(event["timestamp"]),
                partition=event["partition"],
                zone_id=event["zone_id"],
                user=event["user"],
                username=event["username"],
                images=tuple(event["images"]),
            )
            for event in data["events"]
        ),
    )
