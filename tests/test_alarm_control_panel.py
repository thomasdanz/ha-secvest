"""Tests for the alarm panel per partition (#15)."""

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.secvest.const import CONF_PARTITIONS

from .common import PANEL, Setup, coordinator_of
from .fake_panel import FakePanel


async def test_one_panel_per_selected_partition(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Only selected partitions the panel reports get a panel."""
    entry = await setup(**{CONF_PARTITIONS: [1, 3, 9]})
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    panels = sorted(
        entity.unique_id
        for entity in entities
        if entity.domain == "alarm_control_panel"
    )
    assert panels == [
        f"{entry.entry_id}_partition_1_alarm",
        f"{entry.entry_id}_partition_3_alarm",
    ]
    state = hass.states.get(PANEL)
    assert state is not None
    assert state.state == AlarmControlPanelState.DISARMED
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    assert state.attributes["code_arm_required"] is True
    assert state.attributes["code_format"] == "number"
    assert state.attributes["friendly_name"] == "Alarmanlage Teilber. 1"
    assert hass.states.get("alarm_control_panel.alarmanlage_teilber_3") is not None


@pytest.mark.parametrize(
    ("panel_state", "expected", "raw", "acknowledged"),
    [
        ("unset", AlarmControlPanelState.DISARMED, "unset", False),
        ("partset", AlarmControlPanelState.ARMED_HOME, "partset", False),
        ("set", AlarmControlPanelState.ARMED_AWAY, "set", False),
        ("set-alarm", AlarmControlPanelState.TRIGGERED, "set-alarm", False),
        ("partset-alarm", AlarmControlPanelState.TRIGGERED, "partset-alarm", False),
        ("unset-alarm", AlarmControlPanelState.TRIGGERED, "unset-alarm", False),
        # the app's spelling is normalised
        ("set_alarm", AlarmControlPanelState.TRIGGERED, "set-alarm", False),
        ("acknowledged", AlarmControlPanelState.TRIGGERED, "acknowledged", True),
        ("alarm", AlarmControlPanelState.TRIGGERED, "alarm", False),
        ("armed-somehow", STATE_UNKNOWN, "armed-somehow", False),
    ],
)
async def test_states(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_state: str,
    expected: str,
    raw: str,
    acknowledged: bool,
) -> None:
    """The partition state maps to the panel state; the raw value is kept."""
    entry = await setup()
    fake_panel.partitions[1].state = panel_state
    await coordinator_of(entry).async_refresh()
    state = hass.states.get(PANEL)
    assert state is not None
    assert state.state == expected
    assert state.attributes["panel_state"] == raw
    assert state.attributes["acknowledged"] is acknowledged


async def test_unknown_state_is_logged(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown state is logged once."""
    entry = await setup()
    fake_panel.partitions[1].state = "new-state-for-logging"
    await coordinator_of(entry).async_refresh()
    await coordinator_of(entry).async_refresh()
    assert caplog.text.count("'new-state-for-logging'") == 1


async def test_partition_disappears(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A partition the panel stops reporting makes its panel unavailable."""
    entry = await setup()
    del fake_panel.partitions[1]
    await coordinator_of(entry).async_refresh()
    state = hass.states.get(PANEL)
    assert state is not None
    assert state.state == "unavailable"
