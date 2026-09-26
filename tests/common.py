"""Helpers shared by the integration tests."""

from collections.abc import Awaitable, Callable

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.coordinator import SecvestCoordinator

# one polling round with partition 1 selected
ROUND = [
    ("GET", "/system/partitions/"),
    ("GET", "/alarms/"),
    ("GET", "/faults/"),
    ("GET", "/system/partitions-1/zones/"),
]

type Setup = Callable[..., Awaitable[MockConfigEntry]]


def coordinator_of(entry: MockConfigEntry) -> SecvestCoordinator:
    """Return the coordinator of a loaded entry."""
    coordinator: SecvestCoordinator = entry.runtime_data
    return coordinator
