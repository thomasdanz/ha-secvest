"""Tests for the zone devices and sensors (#24, #26)."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.secvest.const import (
    CONF_PARTITIONS,
    CONF_ZONE_DEVICE_CLASSES,
)

from .common import Setup, coordinator_of
from .fake_panel import FakePanel

ZONE = "binary_sensor.room_6_l"


async def test_one_device_per_zone(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Each zone of the selected partitions is a device below the panel."""
    entry = await setup()
    zone_ids = fake_panel.partitions[1].zone_ids
    registry = er.async_get(hass)
    entities = [
        entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.unique_id.endswith("_open")
    ]
    assert sorted(e.unique_id for e in entities) == sorted(
        f"{entry.entry_id}_zone_{zone_id}_open" for zone_id in zone_ids
    )
    # works with the device registry of both supported Home Assistant versions
    devices = {
        identifier: device
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for _, identifier in device.identifiers
    }
    assert len(devices) == 1 + len(zone_ids)
    panel = devices[entry.entry_id]
    assert panel.name == "Alarmanlage"
    device = devices[f"{entry.entry_id}_zone_209"]
    assert device.name == "Room 6 L"
    assert device.manufacturer == "ABUS"
    assert device.via_device_id == panel.id
    entity = registry.async_get(ZONE)
    assert entity is not None
    assert entity.device_id == device.id


async def test_zone_states(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Open is on, closed is off, anything else is unknown but kept."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    state = hass.states.get(ZONE)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes == {
        "friendly_name": "Room 6 L",
        "zone_id": "209",
        "zone_state": "closed",
        "partitions": [1],
        "omittable": True,
        "omitted": False,
        "inner": True,
    }
    fake_panel.open_zone("209")
    await coordinator.async_refresh()
    assert hass.states.get(ZONE).state == STATE_ON  # type: ignore[union-attr]
    fake_panel.zones["209"].state = "tamper"
    await coordinator.async_refresh()
    state = hass.states.get(ZONE)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["zone_state"] == "tamper"


async def test_zone_in_several_partitions(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A zone in several selected partitions is created once."""
    fake_panel.partitions[2].zone_ids.append("209")
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    registry = er.async_get(hass)
    ids = [
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    ]
    assert ids.count(f"{entry.entry_id}_zone_209_open") == 1
    assert hass.states.get(ZONE).attributes["partitions"] == [1, 2]  # type: ignore[union-attr]


async def test_only_selected_partitions(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Zones of partitions that aren't selected get no entities."""
    fake_panel.partitions[2].zone_ids.append("209")
    await setup(**{CONF_PARTITIONS: [2]})
    zones = [
        state.entity_id
        for state in hass.states.async_all("binary_sensor")
        if "zone_id" in state.attributes
    ]
    assert zones == [ZONE]


async def test_device_class_from_options(hass: HomeAssistant, setup: Setup) -> None:
    """The device class comes from the options; invalid values are ignored."""
    await setup(**{CONF_ZONE_DEVICE_CLASSES: {"209": "door", "201": "nonsense"}})
    state = hass.states.get(ZONE)
    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.DOOR
    room = hass.states.get("binary_sensor.room_1")
    assert room is not None
    assert ATTR_DEVICE_CLASS not in room.attributes


async def test_zone_disappears(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A zone the panel stops reporting becomes unavailable."""
    entry = await setup()
    fake_panel.partitions[1].zone_ids.remove("209")
    await coordinator_of(entry).async_refresh()
    assert hass.states.get(ZONE).state == STATE_UNAVAILABLE  # type: ignore[union-attr]


PROBLEM = "binary_sensor.room_6_l_problem"


async def test_zone_problem(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A problem is a state other than open/closed or a fault on the zone."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    registry = er.async_get(hass)
    entity = registry.async_get(PROBLEM)
    assert entity is not None
    assert entity.unique_id == f"{entry.entry_id}_zone_209_problem"
    state = hass.states.get(PROBLEM)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.PROBLEM

    # an open zone is no problem, although the panel lists it as a fault
    fake_panel.open_zone("209")
    await coordinator.async_refresh()
    assert hass.states.get(PROBLEM).state == STATE_OFF  # type: ignore[union-attr]

    fake_panel.zones["209"].state = "tamper"
    await coordinator.async_refresh()
    assert hass.states.get(PROBLEM).state == STATE_ON  # type: ignore[union-attr]

    fake_panel.zones["209"].state = "closed"
    fake_panel.static_faults.append(
        {
            "type": "1234",
            "id": "42",
            "ui-string": "Z209 battery",
            "affects-partition": ["1"],
            "affects-zone": "209",
            "prevents-set": False,
            "prevents-reset": False,
            "is-rf-warning": True,
        }
    )
    await coordinator.async_refresh()
    assert hass.states.get(PROBLEM).state == STATE_ON  # type: ignore[union-attr]
    assert hass.states.get("binary_sensor.room_1_problem").state == STATE_OFF  # type: ignore[union-attr]
