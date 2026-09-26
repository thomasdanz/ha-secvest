"""Tests for the installer lock (#21)."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.const import DOMAIN

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_installer_lock"
    )
    assert entity_id is not None
    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    return entity_id


async def test_lock_and_unlock(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The sensor follows the lock; a locked round costs one request."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    entity_id = _entity_id(hass, entry)
    assert entity_id == "binary_sensor.alarmanlage_installer_lock"
    assert hass.states.get(entity_id).state == STATE_OFF  # type: ignore[union-attr]
    state = coordinator.data

    fake_panel.installer_locked = True
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert hass.states.get(entity_id).state == STATE_ON  # type: ignore[union-attr]
    # each locked round stops at its first request; no backoff, last state kept
    assert fake_panel.stats.requests[len(ROUND) :] == [
        ("GET", "/system/partitions/"),
        ("GET", "/system/partitions/"),
    ]
    assert coordinator.backoff.failures == 0
    assert coordinator.last_exception.retry_after is None  # type: ignore[union-attr]
    assert coordinator.data is state
    assert coordinator.available

    fake_panel.installer_locked = False
    await coordinator.async_refresh()
    assert hass.states.get(entity_id).state == STATE_OFF  # type: ignore[union-attr]
    # the first round after the lock refreshes everything
    assert fake_panel.stats.requests[-len(ROUND) :] == ROUND
    assert coordinator.data is not state


async def test_locked_at_setup(fake_panel: FakePanel, setup: Setup) -> None:
    """A lock at setup lets Home Assistant retry the setup later."""
    fake_panel.installer_locked = True
    entry: MockConfigEntry = await setup()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert fake_panel.stats.requests == [("GET", "/system/partitions/")]
