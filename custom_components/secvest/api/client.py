"""The API operations the integration uses.

The only place that knows the panel's paths, request bodies and filters.
Each method sends exactly the documented request and returns models; errors
from the transport pass through unchanged. No retries and no verification:
that is the caller's job.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import re
from urllib.parse import quote

from .models import LogEntry, PanelEvent, Partition, PartitionState, System, Zone
from .parsing import (
    parse_alarms,
    parse_faults,
    parse_log,
    parse_partition,
    parse_partitions,
    parse_system,
    parse_zone,
    parse_zones,
)
from .transport import LOG_READ_TIMEOUT, Transport

# the states the official app sends; the alarm states are never sent
COMMAND_STATES = frozenset(
    {
        PartitionState.SET,
        PartitionState.PARTSET,
        PartitionState.UNSET,
        PartitionState.ACKNOWLEDGED,
    }
)

_ZONE_ID = re.compile(r"[0-9]+")


class Client:
    """High-level access to one panel."""

    def __init__(self, transport: Transport) -> None:
        """Use the given transport for all requests."""
        self.transport = transport

    @asynccontextmanager
    async def hold(self, *, priority: bool = False) -> AsyncIterator[None]:
        """Hold the request queue for a sequence of operations (see Transport)."""
        async with self.transport.hold(priority=priority):
            yield

    async def get_system(self) -> System:
        """Read the installation's name and partition numbers."""
        return parse_system(await self.transport.request("GET", "/system/"))

    async def get_partitions(self) -> list[Partition]:
        """Read all partitions with their states and zone ids."""
        data = await self.transport.request("GET", "/system/partitions/")
        return parse_partitions(data)

    async def get_zones(self, partition: int) -> list[Zone]:
        """Read the zones of a partition."""
        path = f"/system/partitions-{_partition(partition)}/zones/"
        return parse_zones(await self.transport.request("GET", path))

    async def get_alarms(self) -> list[PanelEvent]:
        """Read the active alarms."""
        return parse_alarms(await self.transport.request("GET", "/alarms/"))

    async def get_faults(self) -> list[PanelEvent]:
        """Read the current faults."""
        return parse_faults(await self.transport.request("GET", "/faults/"))

    async def get_log(self) -> list[LogEntry]:
        """Read the whole log (up to 600 entries); slow, use rarely."""
        data = await self.transport.request(
            "GET", "/logs/", read_timeout=LOG_READ_TIMEOUT
        )
        return parse_log(data)

    async def get_log_since(self, timestamp: int) -> list[LogEntry]:
        """Read the log entries at or after a panel timestamp."""
        if timestamp < 0:
            raise ValueError("timestamp must not be negative")
        # the only filter the firmware knows
        query = quote(f"timestamp ge {int(timestamp)}")
        data = await self.transport.request(
            "GET", f"/logs/?$filter={query}", read_timeout=LOG_READ_TIMEOUT
        )
        return parse_log(data)

    async def set_partition_state(
        self, partition: int, state: PartitionState
    ) -> Partition:
        """Arm, arm internally, disarm or acknowledge; return the panel's answer.

        The answer can still show the old state (a silently ignored command),
        so the caller has to compare it and verify.
        """
        if state not in COMMAND_STATES:
            raise ValueError(f"state {state!r} is never sent to the panel")
        path = f"/system/partitions-{_partition(partition)}/"
        data = await self.transport.request("PUT", path, {"state": str(state)})
        return parse_partition(data)

    async def set_zone_omitted(
        self, partition: int, zone_id: str, omitted: bool
    ) -> Zone:
        """Omit a zone or include it again; return the panel's answer."""
        if not _ZONE_ID.fullmatch(zone_id):
            raise ValueError(f"invalid zone id {zone_id!r}")
        path = f"/system/partitions-{_partition(partition)}/zones-{zone_id}/"
        # the panel expects the value as a string
        body = {"omitted": "true" if omitted else "false"}
        return parse_zone(await self.transport.request("PUT", path, body))


def _partition(number: int) -> int:
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError(f"invalid partition number {number!r}")
    return number
