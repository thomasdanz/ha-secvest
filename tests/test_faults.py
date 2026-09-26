"""Tests for the faults sensor and the panel's problem sensor (#29)."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from .common import Setup, coordinator_of
from .fake_panel import FakePanel

FAULTS = "sensor.alarmanlage_faults"
PROBLEM = "binary_sensor.alarmanlage_problem"

REPEATER_BATTERY = {
    "type": "1170",
    "id": "1104",
    "ui-string": "REP01 Batt schwach",
    "affects-partition": ["1", "2", "3", "4"],
    "prevents-set": False,
    "prevents-reset": False,
    "is-rf-warning": False,
}


async def test_no_faults(hass: HomeAssistant, setup: Setup) -> None:
    """Without faults the count is 0 and there is no problem."""
    await setup()
    state = hass.states.get(FAULTS)
    assert state is not None
    assert state.state == "0"
    assert state.attributes["faults"] == []
    assert state.attributes["summary"] == ""
    problem = hass.states.get(PROBLEM)
    assert problem is not None
    assert problem.state == STATE_OFF
    assert problem.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.PROBLEM


async def test_faults(hass: HomeAssistant, fake_panel: FakePanel, setup: Setup) -> None:
    """All faults are listed; only those other than an open zone are a problem."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.open_zone("209")
    await coordinator.async_refresh()
    state = hass.states.get(FAULTS)
    assert state is not None
    assert state.state == "1"
    assert hass.states.get(PROBLEM).state == STATE_OFF  # type: ignore[union-attr]

    fake_panel.static_faults.append(REPEATER_BATTERY)
    await coordinator.async_refresh()
    state = hass.states.get(FAULTS)
    assert state is not None
    assert state.state == "2"
    assert state.attributes["faults"][0] == {
        "type": "1170",
        "id": "1104",
        "text": "REP01 Batt schwach",
        "partitions": [1, 2, 3, 4],
        "zone": None,
        "prevents_set": False,
        "prevents_reset": False,
        "is_rf_warning": False,
    }
    assert state.attributes["faults"][1]["zone"] == "209"
    assert state.attributes["summary"] == "REP01 Batt schwach\nZ209 A Room 6 L"
    assert hass.states.get(PROBLEM).state == STATE_ON  # type: ignore[union-attr]


async def test_fault_without_text(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A fault without text is summarised by type and id."""
    entry = await setup()
    fault = {
        key: value for key, value in REPEATER_BATTERY.items() if key != "ui-string"
    }
    fake_panel.static_faults.append(fault)
    await coordinator_of(entry).async_refresh()
    state = hass.states.get(FAULTS)
    assert state is not None
    assert state.attributes["summary"] == "Fault 1170/1104"
