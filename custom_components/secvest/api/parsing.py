"""Lenient parsing of the panel's JSON responses.

The panel's JSON is not always clean: strings may contain raw control
characters, ids and numbers arrive as strings, and states or types may take
values this client doesn't know. Parsing accepts all of that. Unknown enum
values are kept as raw strings and logged once. Missing required fields or
values of the wrong kind raise CommunicationError.
"""

from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from enum import StrEnum
import json
import logging
from typing import Any

from .errors import CommunicationError
from .models import (
    AlarmType,
    FaultType,
    LogEntry,
    LogEvent,
    LogType,
    PanelEvent,
    Partition,
    PartitionState,
    System,
    Zone,
    ZoneState,
)

_LOGGER = logging.getLogger(__name__)

# (enum name, value) pairs already logged, so each unknown value is logged once
_reported_unknown: set[tuple[str, str]] = set()


def loads(data: bytes | str) -> Any:
    """Decode a response body, tolerating raw control characters in strings."""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    try:
        return json.loads(data, strict=False)
    except json.JSONDecodeError as err:
        raise CommunicationError(f"invalid JSON in response: {err}") from err


def panel_time(timestamp: int, tz: tzinfo) -> datetime:
    """Convert a log timestamp to an aware datetime.

    The panel encodes its local wall-clock time as Unix seconds, so the value
    is read as UTC and then placed in the panel's time zone. In the hour that
    repeats when daylight saving time ends, the earlier occurrence is used.
    """
    wall_clock = datetime.fromtimestamp(timestamp, UTC)
    return wall_clock.replace(tzinfo=tz)


def parse_system(data: Any) -> System:
    """Parse GET /system/."""
    obj = _object(data)
    return System(
        name=_str(obj, "name"),
        partitions=tuple(
            _int(value, "partitions") for value in _list(obj, "partitions")
        ),
    )


def parse_partitions(data: Any) -> list[Partition]:
    """Parse GET /system/partitions/."""
    return [parse_partition(item) for item in _array(data)]


def parse_partition(data: Any) -> Partition:
    """Parse one partition (GET or PUT /system/partitions-{n}/)."""
    obj = _object(data)
    return Partition(
        number=_int(_required(obj, "id"), "id"),
        name=_str(obj, "name"),
        state=_partition_state(_str(obj, "state")),
        zone_ids=tuple(_id(value, "zones") for value in _list(obj, "zones")),
    )


def parse_zones(data: Any) -> list[Zone]:
    """Parse GET /system/partitions-{n}/zones/."""
    return [parse_zone(item) for item in _array(data)]


def parse_zone(data: Any) -> Zone:
    """Parse one zone (GET or PUT /system/partitions-{n}/zones-{zone}/)."""
    obj = _object(data)
    return Zone(
        id=_id(_required(obj, "id"), "id"),
        name=_str(obj, "name"),
        state=_enum(ZoneState, _str(obj, "state")),
        inner=_bool(_required(obj, "inner"), "inner"),
        omittable=_bool(_required(obj, "omittable"), "omittable"),
        omitted=_bool(_required(obj, "omitted"), "omitted"),
    )


def parse_faults(data: Any) -> list[PanelEvent]:
    """Parse GET /faults/, also the faults in a 409 response to arming."""
    return [_parse_event(item, FaultType) for item in _array(data)]


def parse_alarms(data: Any) -> list[PanelEvent]:
    """Parse GET /alarms/."""
    return [_parse_event(item, AlarmType) for item in _array(data)]


def parse_log(data: Any) -> list[LogEntry]:
    """Parse GET /logs/, with or without a filter."""
    return [_parse_log_entry(item) for item in _array(data)]


def _parse_event(data: Any, types: type[FaultType] | type[AlarmType]) -> PanelEvent:
    obj = _object(data)
    partitions = obj.get("affects-partition")
    if partitions is None:
        partitions = []
    elif not isinstance(partitions, list):
        partitions = [partitions]
    return PanelEvent(
        type=_enum(types, _id(_required(obj, "type"), "type")),
        id=_id(_required(obj, "id"), "id"),
        text=_optional_str(obj, "ui-string"),
        partitions=tuple(_int(value, "affects-partition") for value in partitions),
        zone_id=_optional_id(obj, "affects-zone"),
        prevents_set=_optional_bool(obj, "prevents-set"),
        prevents_reset=_optional_bool(obj, "prevents-reset"),
        is_rf_warning=_optional_bool(obj, "is-rf-warning"),
    )


def _parse_log_entry(data: Any) -> LogEntry:
    obj = _object(data)
    return LogEntry(
        id=_id(_required(obj, "id"), "id"),
        type=_enum(LogType, _str(obj, "type")),
        text=_str(obj, "desc"),
        events=tuple(_parse_log_event(item) for item in _list(obj, "events")),
    )


def _parse_log_event(data: Any) -> LogEvent:
    obj = _object(data)
    index = obj.get("partition")
    user = obj.get("user")
    images = obj.get("images")
    return LogEvent(
        timestamp=_int(_required(obj, "timestamp"), "timestamp"),
        # the log counts partitions from zero, everything else from one
        partition=None if index is None else _int(index, "partition") + 1,
        zone_id=_optional_id(obj, "zone"),
        user=None if user is None else _int(user, "user"),
        username=_optional_str(obj, "username"),
        images=tuple(_as_str(value, "images") for value in _array(images or [])),
    )


def _partition_state(value: str) -> PartitionState | str:
    # accept set_alarm next to set-alarm (the app's spelling)
    return _enum(PartitionState, value.replace("_", "-"), raw=value)


def _enum[E: StrEnum](enum: type[E], value: str, raw: str | None = None) -> E | str:
    try:
        return enum(value)
    except ValueError:
        raw = value if raw is None else raw
        if (enum.__name__, raw) not in _reported_unknown:
            _reported_unknown.add((enum.__name__, raw))
            _LOGGER.warning("Unknown %s from the panel: %r", enum.__name__, raw)
        return raw


def _object(data: Any) -> Mapping[str, Any]:
    if not isinstance(data, dict):
        raise CommunicationError(f"expected an object, got {type(data).__name__}")
    return data


def _array(data: Any) -> list[Any]:
    if not isinstance(data, list):
        raise CommunicationError(f"expected an array, got {type(data).__name__}")
    return data


def _required(obj: Mapping[str, Any], key: str) -> Any:
    if key not in obj or obj[key] is None:
        raise CommunicationError(f"missing field {key!r}")
    return obj[key]


def _list(obj: Mapping[str, Any], key: str) -> list[Any]:
    value = _required(obj, key)
    if not isinstance(value, list):
        raise CommunicationError(f"field {key!r} is not an array")
    return value


def _str(obj: Mapping[str, Any], key: str) -> str:
    return _as_str(_required(obj, key), key)


def _optional_str(obj: Mapping[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return None if value is None else _as_str(value, key)


def _as_str(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise CommunicationError(f"field {key!r} is not a string")
    return value


def _id(value: Any, key: str) -> str:
    """Ids are strings, but a number is accepted as well."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise CommunicationError(f"field {key!r} is not an id")


def _optional_id(obj: Mapping[str, Any], key: str) -> str | None:
    value = obj.get(key)
    return None if value is None else _id(value, key)


def _int(value: Any, key: str) -> int:
    """Numbers usually arrive as strings; both forms are accepted."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise CommunicationError(f"field {key!r} is not a number")


def _bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise CommunicationError(f"field {key!r} is not a boolean")


def _optional_bool(obj: Mapping[str, Any], key: str) -> bool | None:
    value = obj.get(key)
    return None if value is None else _bool(value, key)
