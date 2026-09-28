"""Tests for acknowledging an alarm (#20).

Not tested at a real panel: that would need a real alarm. The behaviour
follows the specification (firmware analysis and the official app).
"""

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.commands import CommandError, async_acknowledge

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel, Injection

PANEL = "alarm_control_panel.alarmanlage_teilber_1"
BUTTON = "button.alarmanlage_teilber_1_acknowledge_alarm"
PUT = ("PUT", "/system/partitions-1/")


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def _alarm(fake_panel: FakePanel, entry: MockConfigEntry) -> None:
    fake_panel.partitions[1].state = "set"
    fake_panel.trigger_alarm(1, "209")
    await coordinator_of(entry).async_refresh()


async def test_acknowledge(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The button exists during an alarm only and acknowledges it."""
    entry = await setup()
    assert _state(hass, BUTTON) == STATE_UNAVAILABLE
    await _alarm(fake_panel, entry)
    assert _state(hass, BUTTON) != STATE_UNAVAILABLE
    sent = len(fake_panel.stats.requests)
    await hass.services.async_call(
        "button", "press", {"entity_id": BUTTON}, blocking=True
    )
    assert fake_panel.partitions[1].state == "acknowledged"
    assert fake_panel.stats.requests[sent:] == [PUT, *ROUND]
    # still triggered, but acknowledged; the button is gone again
    assert _state(hass, PANEL) == AlarmControlPanelState.TRIGGERED
    state = hass.states.get(PANEL)
    assert state is not None
    assert state.attributes["acknowledged"] is True
    assert _state(hass, BUTTON) == STATE_UNAVAILABLE


async def test_disarm_during_alarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Disarming acknowledges first; unset is never sent from an alarm."""
    entry = await setup()
    await _alarm(fake_panel, entry)
    sent = len(fake_panel.stats.requests)
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
    )
    assert _state(hass, PANEL) == AlarmControlPanelState.DISARMED
    assert fake_panel.stats.requests[sent:] == [PUT, *ROUND, PUT, *ROUND]
    # the fake panel would record a violation for unset during an alarm


async def test_disarm_from_acknowledged(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """After acknowledging, disarming is a single command."""
    entry = await setup()
    await _alarm(fake_panel, entry)
    await async_acknowledge(coordinator_of(entry), 1)
    sent = len(fake_panel.stats.requests)
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
    )
    assert _state(hass, PANEL) == AlarmControlPanelState.DISARMED
    assert fake_panel.stats.requests[sent:] == [PUT, *ROUND]


async def test_acknowledging_first_fails(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed acknowledgement stops disarming and is named."""
    entry = await setup()
    await _alarm(fake_panel, entry)
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "status", status=500))
    with pytest.raises(CommandError) as err:
        await hass.services.async_call(
            "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
        )
    assert err.value.translation_key == "acknowledge_first_failed_error"
    assert "acknowledging the alarm first failed" in str(err.value)
    assert _state(hass, PANEL) == AlarmControlPanelState.TRIGGERED
    assert fake_panel.stats.requests.count(PUT) == 1


async def test_no_arming_during_an_alarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming during an alarm isn't sent at all."""
    entry = await setup()
    await _alarm(fake_panel, entry)
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await hass.services.async_call(
            "alarm_control_panel", "alarm_arm_home", {"entity_id": PANEL}, blocking=True
        )
    assert err.value.translation_key == "arm_during_alarm"
    assert len(fake_panel.stats.requests) == sent


async def test_nothing_to_acknowledge(fake_panel: FakePanel, setup: Setup) -> None:
    """Without an alarm, acknowledging sends nothing."""
    entry = await setup()
    with pytest.raises(CommandError) as err:
        await async_acknowledge(coordinator_of(entry), 1)
    assert err.value.translation_key == "no_alarm"
    assert fake_panel.stats.requests == ROUND
