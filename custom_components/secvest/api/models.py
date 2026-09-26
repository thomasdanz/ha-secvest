"""Data parsed from the panel's responses.

Values the panel may extend (states, types) are enums where known; unknown
values are kept as the raw string instead of failing (see parsing.py).
Partitions are always identified by their one-based number, whichever form
the panel used.
"""

from dataclasses import dataclass
from enum import StrEnum


class PartitionState(StrEnum):
    """State of a partition, including the alarm states."""

    UNSET = "unset"
    PARTSET = "partset"
    SET = "set"
    UNSET_ALARM = "unset-alarm"
    PARTSET_ALARM = "partset-alarm"
    SET_ALARM = "set-alarm"
    ACKNOWLEDGED = "acknowledged"


class ZoneState(StrEnum):
    """State of a zone; only open and closed have been observed."""

    OPEN = "open"
    CLOSED = "closed"
    TAMPER = "tamper"
    FAULT = "fault"
    ZONE_FAULT = "zoneFault"


class FaultType(StrEnum):
    """Fault classes seen so far."""

    REPEATER_BATTERY_LOW = "1170"
    ZONE_OPEN = "5000"


class AlarmType(StrEnum):
    """Alarm classes as defined by the official Android app (not observed)."""

    UNDEFINED = "0"
    FAULT = "1"
    TECHNICAL = "2"
    FIRE = "3"
    BURGLARY = "4"
    MEDICAL = "6"
    PANIC = "7"
    SOCIAL = "8"
    INACTIVITY = "9"


class LogType(StrEnum):
    """Category of a log entry."""

    NORMAL = "normal"
    ALARM = "alarm"
    TROUBLE = "trouble"


@dataclass(frozen=True, slots=True)
class System:
    """Name of the installation and its partitions."""

    name: str
    partitions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Partition:
    """A partition with its state and the ids of its zones."""

    number: int
    name: str
    state: PartitionState | str
    zone_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Zone:
    """A zone (detector) of a partition."""

    id: str
    name: str
    state: ZoneState | str
    inner: bool
    omittable: bool
    omitted: bool


@dataclass(frozen=True, slots=True)
class PanelEvent:
    """A fault or an alarm; both use the same format.

    Only type and id are required; the other fields are None or empty when
    the panel doesn't send them.
    """

    type: FaultType | AlarmType | str
    id: str
    text: str | None
    partitions: tuple[int, ...]
    zone_id: str | None
    prevents_set: bool | None
    prevents_reset: bool | None
    is_rf_warning: bool | None


@dataclass(frozen=True, slots=True)
class LogEvent:
    """Details of a log entry.

    The timestamp is the panel's local wall-clock time encoded as Unix
    seconds; convert it with parsing.panel_time().
    """

    timestamp: int
    partition: int | None
    zone_id: str | None
    user: int | None
    username: str | None
    images: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LogEntry:
    """An entry of the panel's log."""

    id: str
    type: LogType | str
    text: str
    events: tuple[LogEvent, ...]
