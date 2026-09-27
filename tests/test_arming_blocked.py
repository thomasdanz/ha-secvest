"""Tests for arming blocked and open zones per partition (#30)."""

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.secvest.const import CONF_PARTITIONS

from .common import Setup, coordinator_of
from .fake_panel import FakePanel

BLOCKED = "binary_sensor.alarmanlage_teilber_1_arming_blocked"
OPEN_ZONES = "sensor.alarmanlage_teilber_1_open_zones"


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_arming_blocked(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Blocked while open, not omitted zones exist; counted by open zones."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    coordinator = coordinator_of(entry)
    registry = er.async_get(hass)
    entity = registry.async_get(BLOCKED)
    assert entity is not None
    assert entity.unique_id == f"{entry.entry_id}_partition_1_arming_blocked"
    open_zones = registry.async_get(OPEN_ZONES)
    assert open_zones is not None
    assert open_zones.unique_id == f"{entry.entry_id}_partition_1_open_zones"
    state = hass.states.get(BLOCKED)
    assert state is not None
    assert state.state == STATE_OFF
    assert "device_class" not in state.attributes
    assert state.attributes["friendly_name"] == "Alarmanlage Teilber. 1 arming blocked"
    assert state.attributes["blocking_zones"] == []
    assert _state(hass, OPEN_ZONES) == "0"

    fake_panel.open_zone("209")
    await coordinator.async_refresh()
    assert _state(hass, BLOCKED) == STATE_ON
    assert _state(hass, OPEN_ZONES) == "1"
    attributes = hass.states.get(BLOCKED).attributes  # type: ignore[union-attr]
    assert attributes["blocking_zones"] == ["209"]
    assert attributes["blocking_faults"] == []
    zones = hass.states.get(OPEN_ZONES).attributes  # type: ignore[union-attr]
    assert zones["zones"] == ["209"]
    assert zones["summary"] == "Room 6 L"
    # the zone belongs to partition 1 only
    assert _state(hass, "binary_sensor.alarmanlage_teilber_2_arming_blocked") == (
        STATE_OFF
    )


async def test_entry_door_blocks(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An open entry door is no fault, but arming via the API fails then."""
    entry = await setup()
    fake_panel.open_zone("219")
    await coordinator_of(entry).async_refresh()
    assert coordinator_of(entry).data.faults == ()
    assert _state(hass, BLOCKED) == STATE_ON
    assert _state(hass, OPEN_ZONES) == "1"


async def test_omitted_zone_doesnt_block(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An open zone that is omitted doesn't count."""
    entry = await setup()
    fake_panel.open_zone("209")
    fake_panel.zones["209"].omitted = True
    await coordinator_of(entry).async_refresh()
    assert _state(hass, BLOCKED) == STATE_OFF
    assert _state(hass, OPEN_ZONES) == "0"


async def test_other_blocking_fault(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A fault other than an open zone that prevents arming blocks too."""
    entry = await setup()
    fake_panel.static_faults.append(
        {
            "type": "1234",
            "id": "7",
            "ui-string": "Sabotage",
            "affects-partition": ["1"],
            "prevents-set": True,
            "prevents-reset": False,
            "is-rf-warning": False,
        }
    )
    await coordinator_of(entry).async_refresh()
    assert _state(hass, BLOCKED) == STATE_ON
    assert _state(hass, OPEN_ZONES) == "0"
    attributes = hass.states.get(BLOCKED).attributes  # type: ignore[union-attr]
    assert attributes["blocking_faults"] == ["Sabotage"]


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
    assert _state(hass, BLOCKED) == STATE_UNAVAILABLE
    assert _state(hass, OPEN_ZONES) == STATE_UNAVAILABLE
