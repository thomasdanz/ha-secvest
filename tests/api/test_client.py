"""Tests for the API operations (#75), against the fake panel."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from custom_components.secvest.api.client import Client
from custom_components.secvest.api.errors import (
    ArmingBlockedError,
    NotAllowedError,
    NotFoundError,
)
from custom_components.secvest.api.models import (
    FaultType,
    LogType,
    PartitionState,
    ZoneState,
)
from custom_components.secvest.api.transport import LOG_READ_TIMEOUT, Transport

from ..fake_panel import FakePanel


@pytest.fixture
async def client(fake_panel: FakePanel) -> AsyncIterator[Client]:
    """Return a client for the fake panel."""
    transport = Transport(fake_panel.url, fake_panel.user_code, fake_panel.password)
    yield Client(transport)
    await transport.close()


async def test_reads(fake_panel: FakePanel, client: Client) -> None:
    """Each read sends exactly the documented request and returns models."""
    fake_panel.open_zone("209")
    system = await client.get_system()
    partitions = await client.get_partitions()
    zones = await client.get_zones(1)
    alarms = await client.get_alarms()
    faults = await client.get_faults()
    assert system.partitions == (1, 2, 3, 4)
    assert [p.number for p in partitions] == [1, 2, 3, 4]
    assert {z.id: z.state for z in zones}["209"] is ZoneState.OPEN
    assert alarms == []
    assert [(f.type, f.zone_id) for f in faults] == [(FaultType.ZONE_OPEN, "209")]
    assert [path for _, path in fake_panel.stats.requests] == [
        "/system/",
        "/system/partitions/",
        "/system/partitions-1/zones/",
        "/alarms/",
        "/faults/",
    ]


async def test_zones_of_an_empty_partition(client: Client) -> None:
    """A partition without zones returns an empty list."""
    assert await client.get_zones(2) == []


async def test_unknown_partition(client: Client) -> None:
    """An unknown partition is not found."""
    with pytest.raises(NotFoundError):
        await client.get_zones(9)


async def test_log(fake_panel: FakePanel, client: Client) -> None:
    """The whole log is read unfiltered."""
    log = await client.get_log()
    assert len(log) == len(fake_panel.log)
    assert fake_panel.stats.requests == [("GET", "/logs/")]


async def test_log_since(fake_panel: FakePanel, client: Client) -> None:
    """The log filter is exactly the documented one."""
    since = fake_panel.now()
    fake_panel.start_entry_time("219")
    log = await client.get_log_since(since)
    assert [(entry.type, entry.text) for entry in log] == [
        (LogType.NORMAL, "Eing gest. Z219")
    ]
    assert fake_panel.stats.requests == [
        ("GET", f"/logs/?$filter=timestamp%20ge%20{since}")
    ]
    assert await client.get_log_since(since + 3600) == []


async def test_log_uses_the_long_timeout(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Log requests get the long read timeout, other reads the default."""
    timeouts: dict[str, Any] = {}
    request = client.transport.request

    async def spy(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        timeouts[path.split("?", maxsplit=1)[0]] = kwargs.get("read_timeout")
        return await request(method, path, *args, **kwargs)

    monkeypatch.setattr(client.transport, "request", spy)
    await client.get_log()
    await client.get_log_since(1)
    await client.get_faults()
    assert timeouts == {"/logs/": LOG_READ_TIMEOUT, "/faults/": None}


@pytest.mark.parametrize(
    "state", [PartitionState.SET, PartitionState.PARTSET, PartitionState.UNSET]
)
async def test_set_partition_state(
    fake_panel: FakePanel, client: Client, state: PartitionState
) -> None:
    """A command returns the panel's answer."""
    partition = await client.set_partition_state(1, state)
    assert partition.state is state
    assert fake_panel.partitions[1].state == str(state)


async def test_acknowledge(fake_panel: FakePanel, client: Client) -> None:
    """Acknowledging during an alarm, then disarming."""
    await client.set_partition_state(1, PartitionState.SET)
    fake_panel.trigger_alarm(1, "202")
    ack = await client.set_partition_state(1, PartitionState.ACKNOWLEDGED)
    assert ack.state is PartitionState.ACKNOWLEDGED
    unset = await client.set_partition_state(1, PartitionState.UNSET)
    assert unset.state is PartitionState.UNSET


async def test_ignored_command_returns_the_old_state(
    fake_panel: FakePanel, client: Client
) -> None:
    """The panel's answer shows a silently ignored command."""
    fake_panel.open_zone("219")
    partition = await client.set_partition_state(1, PartitionState.SET)
    assert partition.state is PartitionState.UNSET


async def test_arming_blocked(fake_panel: FakePanel, client: Client) -> None:
    """Errors from the transport pass through unchanged."""
    fake_panel.open_zone("209")
    with pytest.raises(ArmingBlockedError) as exc_info:
        await client.set_partition_state(1, PartitionState.SET)
    assert [fault.zone_id for fault in exc_info.value.faults] == ["209"]


@pytest.mark.parametrize(
    "state",
    [
        PartitionState.SET_ALARM,
        PartitionState.PARTSET_ALARM,
        PartitionState.UNSET_ALARM,
        "armed",
    ],
)
async def test_states_never_sent(
    fake_panel: FakePanel, client: Client, state: Any
) -> None:
    """Alarm states and unknown values are refused without a request."""
    with pytest.raises(ValueError, match="never sent"):
        await client.set_partition_state(1, state)
    assert fake_panel.stats.requests == []


async def test_omit_zone(fake_panel: FakePanel, client: Client) -> None:
    """Omitting sends the value as a string and returns the zone."""
    zone = await client.set_zone_omitted(1, "209", True)
    assert zone.omitted is True
    zone = await client.set_zone_omitted(1, "209", False)
    assert zone.omitted is False
    assert fake_panel.stats.requests == [
        ("PUT", "/system/partitions-1/zones-209/"),
        ("PUT", "/system/partitions-1/zones-209/"),
    ]


async def test_omit_zone_not_omittable(client: Client) -> None:
    """A zone that isn't omittable is not allowed."""
    with pytest.raises(NotAllowedError):
        await client.set_zone_omitted(1, "219", True)


@pytest.mark.parametrize(
    ("partition", "zone_id"),
    [(0, "209"), (-1, "209"), (True, "209"), (1, "209/../x"), (1, ""), (1, "z1")],
)
async def test_invalid_arguments(
    fake_panel: FakePanel, client: Client, partition: Any, zone_id: str
) -> None:
    """Invalid partition numbers or zone ids are refused without a request."""
    with pytest.raises(ValueError, match="invalid"):
        await client.set_zone_omitted(partition, zone_id, True)
    assert fake_panel.stats.requests == []


async def test_negative_timestamp(fake_panel: FakePanel, client: Client) -> None:
    """A negative timestamp is refused without a request."""
    with pytest.raises(ValueError, match="negative"):
        await client.get_log_since(-1)
    assert fake_panel.stats.requests == []


async def test_hold(fake_panel: FakePanel, client: Client) -> None:
    """A command sequence holds the queue through the client."""
    async with client.hold(priority=True):
        await client.set_partition_state(1, PartitionState.SET)
        await client.get_partitions()
    assert len(fake_panel.stats.requests) == 2
