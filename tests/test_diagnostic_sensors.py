"""Diagnostic sensors for polling and the connection (#176)."""

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.sensor import significant

from .common import PANEL, Setup, coordinator_of, get_state
from .fake_panel import FakePanel, Injection

ROUND_DURATION = "sensor.alarmanlage_round_duration"
CONNECTION_SETUP = "sensor.alarmanlage_connection_setup"
FULL_HANDSHAKES = "sensor.alarmanlage_full_handshakes"
FAILED_ROUNDS = "sensor.alarmanlage_failed_rounds"


@pytest.mark.parametrize(
    ("value", "rounded"),
    [
        (0.0131, 0.013),
        (0.4249, 0.42),
        (1.74, 1.7),
        (6.249, 6.2),
        (15.4, 15),
        (0, 0),
        (None, None),
    ],
)
def test_two_significant_digits(value: float | None, rounded: float | None) -> None:
    """Two significant digits, whatever the magnitude."""
    assert significant(value) == rounded


async def _enable_all(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    for entity_id in (CONNECTION_SETUP, FULL_HANDSHAKES):
        registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_enabled_by_default(hass: HomeAssistant, setup: Setup) -> None:
    """Round duration and failed rounds are on; the others when looking closer."""
    await setup()
    registry = er.async_get(hass)
    for entity_id in (ROUND_DURATION, FAILED_ROUNDS):
        entity = registry.async_get(entity_id)
        assert entity is not None
        assert entity.disabled_by is None
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
    for entity_id in (CONNECTION_SETUP, FULL_HANDSHAKES):
        entity = registry.async_get(entity_id)
        assert entity is not None
        assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_values_after_rounds(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Duration and connection setup in seconds; a resumed session is no full one."""
    entry = await setup()
    await _enable_all(hass, entry)
    duration = get_state(hass, ROUND_DURATION)
    assert duration.attributes["unit_of_measurement"] == "s"
    assert float(duration.state) > 0
    assert float(duration.state) == significant(float(duration.state))
    assert float(get_state(hass, CONNECTION_SETUP).state) > 0
    full = int(get_state(hass, FULL_HANDSHAKES).state)
    assert full >= 1
    # the panel closes the idle connection; the next round resumes the session
    fake_panel.close_connections()
    await coordinator_of(entry).async_refresh()
    await hass.async_block_till_done()
    assert coordinator_of(entry).client.transport.stats.resumed_handshakes >= 1
    assert int(get_state(hass, FULL_HANDSHAKES).state) == full
    assert get_state(hass, FAILED_ROUNDS).state == "0"


async def test_failed_rounds_count_up(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Each failed round counts, the sensor stays available, success resets."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.inject(
        Injection("GET", "/system/partitions/", "status", status=500, times=3)
    )
    for failures in (1, 2, 3):
        # the test doesn't wait out the backoff
        coordinator.backoff.not_before = 0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        state = get_state(hass, FAILED_ROUNDS)
        assert state.state == str(failures)
        assert state.attributes["paused"] is False
        assert "500" in state.attributes["last_error"]
    # from the third the other entities are unavailable; these are not
    assert get_state(hass, PANEL).state == "unavailable"
    assert get_state(hass, ROUND_DURATION).state != "unavailable"
    coordinator.backoff.not_before = 0
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = get_state(hass, FAILED_ROUNDS)
    assert state.state == "0"
    assert state.attributes["last_error"] is None


async def test_round_duration_without_the_log(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """The slow log read isn't part of the round duration."""
    fake_panel.log_delay = 0.5
    entry = await setup()
    # the first round read the log (the baseline) before the state
    assert coordinator_of(entry).log.has_baseline
    assert float(get_state(hass, ROUND_DURATION).state) < 0.5
