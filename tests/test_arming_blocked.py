"""Tests for the arming blocked sensor per partition (#30)."""

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.secvest.const import CONF_PARTITIONS

from .common import Setup, coordinator_of
from .fake_panel import FakePanel

BLOCKED = "binary_sensor.alarmanlage_teilber_1_arming_blocked"


async def test_arming_blocked(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """On while a fault with prevents-set affects the partition."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    coordinator = coordinator_of(entry)
    entity = er.async_get(hass).async_get(BLOCKED)
    assert entity is not None
    assert entity.unique_id == f"{entry.entry_id}_partition_1_arming_blocked"
    state = hass.states.get(BLOCKED)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["friendly_name"] == "Alarmanlage Teilber. 1 arming blocked"
    assert state.attributes["blocking_zones"] == []

    fake_panel.open_zone("209")
    await coordinator.async_refresh()
    state = hass.states.get(BLOCKED)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["blocking_zones"] == ["209"]
    assert state.attributes["blocking_faults"] == ["Z209 A Room 6 L"]
    # the fault affects partition 1 only
    other = hass.states.get("binary_sensor.alarmanlage_teilber_2_arming_blocked")
    assert other is not None
    assert other.state == STATE_OFF


async def test_faults_that_dont_block(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Faults without prevents-set don't block."""
    entry = await setup()
    fake_panel.static_faults.append(
        {
            "type": "1170",
            "id": "1104",
            "ui-string": "REP01 Batt schwach",
            "affects-partition": ["1"],
            "prevents-set": False,
            "prevents-reset": False,
            "is-rf-warning": False,
        }
    )
    await coordinator_of(entry).async_refresh()
    assert hass.states.get(BLOCKED).state == STATE_OFF  # type: ignore[union-attr]


async def test_partition_disappears(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unavailable while the panel doesn't report the partition."""
    entry = await setup()
    del fake_panel.partitions[1]
    await coordinator_of(entry).async_refresh()
    assert hass.states.get(BLOCKED).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
