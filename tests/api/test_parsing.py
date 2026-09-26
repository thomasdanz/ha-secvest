"""Tests for the lenient parsing of the panel's responses (#5)."""

import ast
from collections.abc import Callable
from datetime import UTC, datetime
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.secvest.api.errors import CommunicationError
from custom_components.secvest.api.models import (
    AlarmType,
    FaultType,
    LogType,
    PartitionState,
    ZoneState,
)
from custom_components.secvest.api.parsing import (
    loads,
    panel_time,
    parse_alarms,
    parse_faults,
    parse_log,
    parse_partition,
    parse_partitions,
    parse_system,
    parse_zone,
    parse_zones,
)

from .conftest import FIXTURES, load_fixture

BERLIN = ZoneInfo("Europe/Berlin")


PARSERS: dict[str, Callable[[Any], object]] = {
    "GET_system": parse_system,
    "GET_system_partitions": parse_partitions,
    "GET_system_partitions-1": parse_partition,
    "GET_system_partitions-2": parse_partition,
    "PUT_system_partitions-1": parse_partition,
    "PUT_system_partitions-2": parse_partition,
    "GET_system_partitions-1_zones": parse_zones,
    "GET_system_partitions-2_zones": parse_zones,
    "GET_system_partitions-1_zones-209": parse_zone,
    "PUT_system_partitions-1_zones-209": parse_zone,
    "GET_faults": parse_faults,
    "GET_alarms": parse_alarms,
    "GET_logs": parse_log,
    "GET_logs_filter-timestamp-ge": parse_log,
}


def _parser_for(name: str) -> Callable[[Any], object] | None:
    """Return the parser for a JSON response fixture, if the client reads it."""
    if name.endswith(".request.json") or ".403." in name:
        return None
    if ".409." in name:
        # a refused arming lists the blocking faults
        return parse_faults
    return PARSERS.get(name.split(".", maxsplit=1)[0])


JSON_RESPONSES = sorted(
    path.name for path in FIXTURES.glob("*.json") if _parser_for(path.name)
)


def test_fixtures_are_covered() -> None:
    """Every JSON response of the specification is parsed here, with reasons."""
    skipped = {
        path.name
        for path in FIXTURES.glob("*.json")
        if path.name not in JSON_RESPONSES and not path.name.endswith(".request.json")
    }
    # outputs and cameras aren't used by the integration; the 403 body is an
    # error response (#4)
    assert skipped == {"GET_cameras.json", "GET_outputs.json", "GET_system.403.json"}


@pytest.mark.parametrize("name", JSON_RESPONSES)
def test_parse_fixture(name: str, caplog: pytest.LogCaptureFixture) -> None:
    """Every response of the specification parses without unknown values."""
    parser = _parser_for(name)
    assert parser is not None
    parser(loads(load_fixture(name)))
    assert not caplog.records


def test_control_characters_in_strings() -> None:
    """Raw control characters inside strings don't break parsing."""
    assert loads(b'{"username": "A\x01B\tC"}') == {"username": "A\x01B\tC"}


def test_invalid_json() -> None:
    """A body that isn't JSON is a communication error."""
    with pytest.raises(CommunicationError):
        loads(b"<html>error</html>")


def test_invalid_utf8_is_replaced() -> None:
    """Bytes that aren't UTF-8 don't break parsing."""
    assert loads(b'{"name": "K\xfcche"}') == {"name": "K�che"}


def test_system() -> None:
    """Partition ids are one-based numbers."""
    system = parse_system(loads(load_fixture("GET_system.json")))
    assert system.partitions == (1, 2, 3, 4)


def test_partitions() -> None:
    """Partitions carry their one-based number, state and zone ids."""
    partitions = parse_partitions(loads(load_fixture("GET_system_partitions.json")))
    assert [p.number for p in partitions] == [1, 2, 3, 4]
    assert partitions[0].state is PartitionState.UNSET
    assert partitions[0].zone_ids[0] == "201"
    assert partitions[1].zone_ids == ()


def test_zone() -> None:
    """A zone keeps its id as a string."""
    zone = parse_zone(loads(load_fixture("GET_system_partitions-1_zones-209.json")))
    assert zone.id == "209"
    assert zone.state in {ZoneState.OPEN, ZoneState.CLOSED}
    assert zone.omittable is True


def test_ids_and_numbers_as_numbers() -> None:
    """Ids and numbers are accepted whether sent as strings or as numbers."""
    zone = parse_zone(
        {
            "id": 209,
            "name": "Zone",
            "state": "open",
            "inner": True,
            "omittable": True,
            "omitted": False,
        }
    )
    assert zone.id == "209"
    partition = parse_partition(
        {"id": 1, "name": "P", "state": "set", "zones": [201, "202"]}
    )
    assert partition.number == 1
    assert partition.zone_ids == ("201", "202")
    entry = parse_log(
        [{"id": 1, "type": "normal", "desc": "x", "events": [{"timestamp": 17}]}]
    )[0]
    assert entry.id == "1"
    assert entry.events[0].timestamp == 17


def test_booleans_as_strings() -> None:
    """Booleans sent as strings (like the app's PUT body) are accepted."""
    zone = parse_zone(
        {
            "id": "209",
            "name": "Zone",
            "state": "open",
            "inner": "true",
            "omittable": "True",
            "omitted": "false",
        }
    )
    assert (zone.inner, zone.omittable, zone.omitted) == (True, True, False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("set-alarm", PartitionState.SET_ALARM),
        ("set_alarm", PartitionState.SET_ALARM),
        ("partset-alarm", PartitionState.PARTSET_ALARM),
        ("partset_alarm", PartitionState.PARTSET_ALARM),
        ("unset-alarm", PartitionState.UNSET_ALARM),
        ("unset_alarm", PartitionState.UNSET_ALARM),
        ("acknowledged", PartitionState.ACKNOWLEDGED),
    ],
)
def test_alarm_states_in_both_spellings(value: str, expected: PartitionState) -> None:
    """Alarm states are accepted with hyphens and with underscores."""
    partition = parse_partition({"id": "1", "name": "P", "state": value, "zones": []})
    assert partition.state is expected


def test_unknown_values_kept_and_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown enum values are kept raw and logged once per value."""
    caplog.set_level(logging.WARNING)
    for _ in range(2):
        partition = parse_partition(
            {"id": "1", "name": "P", "state": "alarm", "zones": []}
        )
        faults = parse_faults([{"type": "9999", "id": "1"}])
        alarms = parse_alarms([{"type": "5", "id": "1"}])
        log = parse_log(
            [{"id": "1", "type": "info", "desc": "x", "events": [{"timestamp": "1"}]}]
        )
        zone = parse_zone(
            {
                "id": "209",
                "name": "Zone",
                "state": "masked",
                "inner": True,
                "omittable": True,
                "omitted": False,
            }
        )
    assert partition.state == "alarm"
    assert faults[0].type == "9999"
    assert alarms[0].type == "5"
    assert log[0].type == "info"
    assert zone.state == "masked"
    assert len(caplog.records) == 5


def test_faults() -> None:
    """Faults carry type, affected partitions and zone."""
    faults = parse_faults(loads(load_fixture("GET_faults.json")))
    battery, zone_open = faults
    assert battery.type is FaultType.REPEATER_BATTERY_LOW
    assert battery.partitions == (1, 2, 3, 4)
    assert battery.zone_id is None
    assert zone_open.type is FaultType.ZONE_OPEN
    assert zone_open.zone_id == "212"
    assert zone_open.prevents_set is True


def test_event_with_identifying_fields_only() -> None:
    """Only type and id are required; affects-partition may be a single value."""
    alarm = parse_alarms([{"type": "4", "id": 7, "affects-partition": 1}])[0]
    assert alarm.type is AlarmType.BURGLARY
    assert alarm.id == "7"
    assert alarm.partitions == (1,)
    assert alarm.text is None
    assert alarm.zone_id is None
    assert alarm.prevents_set is None
    assert alarm.prevents_reset is None
    assert alarm.is_rf_warning is None
    assert parse_faults([{"type": "1170", "id": "1"}])[0].partitions == ()


def test_log_event_with_timestamp_only() -> None:
    """Log events only require a timestamp."""
    entry = parse_log(
        [
            {
                "id": "1",
                "type": "trouble",
                "desc": "x",
                "events": [{"timestamp": "1789473728"}],
            }
        ]
    )[0]
    assert entry.type is LogType.TROUBLE
    event = entry.events[0]
    assert event.timestamp == 1789473728
    assert event.partition is None
    assert event.zone_id is None
    assert event.user is None
    assert event.username is None
    assert event.images == ()


def test_log_event_with_all_fields() -> None:
    """Optional log event fields are parsed when present."""
    event = parse_log(
        [
            {
                "id": "1",
                "type": "alarm",
                "desc": "x",
                "events": [
                    {
                        "timestamp": "1",
                        "partition": "1",
                        "zone": "209",
                        "user": "3",
                        "username": "User",
                        "images": ["img/1.jpg"],
                    }
                ],
            }
        ]
    )[0].events[0]
    assert event.partition == 2
    assert event.zone_id == "209"
    assert event.user == 3
    assert event.username == "User"
    assert event.images == ("img/1.jpg",)


def test_partition_numbering_is_consistent() -> None:
    """The log's zero-based partition index maps to the one-based number."""
    log = parse_log(loads(load_fixture("GET_logs_filter-timestamp-ge.example4.json")))
    partitions = parse_partitions(loads(load_fixture("GET_system_partitions.json")))
    numbers = {e.partition for entry in log for e in entry.events if e.partition}
    assert numbers == {partitions[0].number} == {1}


def test_panel_time() -> None:
    """Log timestamps are the panel's wall-clock time, read as UTC."""
    local = panel_time(1789473728, BERLIN)
    assert local.replace(tzinfo=None) == datetime.fromtimestamp(
        1789473728, UTC
    ).replace(tzinfo=None)
    assert local.utcoffset() is not None
    assert local.tzinfo is BERLIN


def test_panel_time_when_the_clock_goes_back() -> None:
    """In the repeated hour, the earlier occurrence is used."""
    # 2026-10-25 02:30 wall-clock time exists twice in Berlin
    wall_clock = int(datetime(2026, 10, 25, 2, 30, tzinfo=UTC).timestamp())
    local = panel_time(wall_clock, BERLIN)
    assert local.fold == 0
    assert local.astimezone(UTC).hour == 0


@pytest.mark.parametrize(
    ("parser", "data"),
    [
        (parse_zone, {"id": "209"}),
        (parse_partition, {"id": "x", "name": "P", "state": "set", "zones": []}),
        (parse_partitions, {"id": "1"}),
        (parse_faults, [{"type": "5000"}]),
        (parse_log, [{"id": "1", "type": "normal", "desc": "x", "events": [{}]}]),
        (parse_zone, []),
        (
            parse_zone,
            {
                "id": "1",
                "name": "Z",
                "state": "open",
                "inner": "yes",
                "omittable": True,
                "omitted": False,
            },
        ),
    ],
)
def test_malformed_responses(parser: Callable[[Any], object], data: Any) -> None:
    """Missing required fields or wrong types raise a communication error."""
    with pytest.raises(CommunicationError):
        parser(data)


def test_api_package_does_not_import_home_assistant() -> None:
    """The API client stays free of Home Assistant imports (ADR 0002)."""
    package = Path(__file__).parent.parent.parent / "custom_components/secvest/api"
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(m.split(".")[0] == "homeassistant" for m in modules), (
                path.name
            )
