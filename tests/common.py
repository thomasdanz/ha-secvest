"""Helpers shared by the integration tests."""

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import Event, HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.secvest.commands import EVENT_ARMING_FAILED
from custom_components.secvest.coordinator import SecvestCoordinator

# the alarm panel of partition 1, and the code the tests set up for it
PANEL = "alarm_control_panel.alarmanlage_teilber_1"
CODE = "4711"

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


async def poll_log_now(entry: MockConfigEntry) -> None:
    """Run a round that reads the log, without waiting for its interval."""
    coordinator = coordinator_of(entry)
    coordinator._log_due = 0
    await coordinator.async_refresh()


async def options_settings(
    hass: HomeAssistant, entry: MockConfigEntry
) -> ConfigFlowResult:
    """Open the options and choose the settings (partitions, intervals, zones)."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )


def get_state(hass: HomeAssistant, entity_id: str = PANEL) -> State:
    """Return an entity's state, which has to exist."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def state_of(hass: HomeAssistant, entity_id: str = PANEL) -> str:
    """Return an entity's state value."""
    return get_state(hass, entity_id).state


async def call_panel(
    hass: HomeAssistant,
    service: str,
    entity_id: str = PANEL,
    *,
    code: str | None = CODE,
) -> None:
    """Call an alarm panel action, with the code unless it is None."""
    data: dict[str, Any] = {"entity_id": entity_id}
    if code is not None:
        data["code"] = code
    await hass.services.async_call("alarm_control_panel", service, data, blocking=True)


def arming_failed_events(hass: HomeAssistant) -> list[Event[Any]]:
    """Collect the arming_failed events from now on."""
    # a callback, so the events are collected in order in the event loop
    events: list[Event[Any]] = async_capture_events(hass, EVENT_ARMING_FAILED)
    return events
