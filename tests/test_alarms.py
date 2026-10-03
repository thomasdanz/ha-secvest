"""Tests for detecting triggered alarms (#19)."""

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
import pytest

from .common import PANEL, Setup, coordinator_of, state_of
from .fake_panel import FakePanel, Injection


def _attributes(hass: HomeAssistant) -> dict[str, object]:
    state = hass.states.get(PANEL)
    assert state is not None
    return dict(state.attributes)


async def test_alarm_with_details(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The partition state shows the alarm; /alarms/ adds type and zone."""
    entry = await setup()
    assert _attributes(hass)["alarm_type"] is None
    fake_panel.partitions[1].state = "set"
    fake_panel.trigger_alarm(1, "209")
    await coordinator_of(entry).async_refresh()
    assert state_of(hass) == AlarmControlPanelState.TRIGGERED
    attributes = _attributes(hass)
    assert attributes["panel_state"] == "set-alarm"
    assert attributes["alarm_type"] == "burglary"
    assert attributes["alarm_zones"] == ["209"]


async def test_unknown_alarm_type_stays_raw(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An unknown alarm type code is shown as it is."""
    entry = await setup()
    fake_panel.trigger_alarm(1, "209", alarm_type="99")
    await coordinator_of(entry).async_refresh()
    assert _attributes(hass)["alarm_type"] == "99"


async def test_alarm_without_alarms_list(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An empty /alarms/ doesn't hide an alarm in the partition state."""
    entry = await setup()
    fake_panel.partitions[1].state = "partset-alarm"
    await coordinator_of(entry).async_refresh()
    assert state_of(hass) == AlarmControlPanelState.TRIGGERED
    assert _attributes(hass)["alarm_type"] is None


async def test_alarms_failing(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If /alarms/ fails, the round goes on and the state shows the alarm."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.partitions[1].state = "set-alarm"
    fake_panel.inject(Injection("GET", "/alarms/", "drop_before", times=4))
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.backoff.failures == 0
    assert state_of(hass) == AlarmControlPanelState.TRIGGERED
    assert caplog.text.count("Reading the alarms failed") == 1


async def test_alarm_only_in_the_list(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An alarm /alarms/ reports is shown even if the state doesn't say so."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.alarms.append(
        {"type": "3", "id": "1", "affects-partition": "1", "affects-zone": "209"}
    )
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert state_of(hass) == AlarmControlPanelState.TRIGGERED
    assert _attributes(hass)["alarm_type"] == "fire"
    assert caplog.text.count("reports an alarm for partition 1") == 1
