"""Tests for acknowledging an alarm while disarming (#20).

Not tested at a real panel: that would need a real alarm. The behaviour
follows the specification (firmware analysis and the official app).
"""

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.commands import CommandError

from .common import CODE, PANEL, ROUND, Setup, call_panel, coordinator_of, state_of
from .fake_panel import FakePanel, Injection

PUT = ("PUT", "/system/partitions-1/")
# the fresh read before deciding the sequence
READ = ("GET", "/system/partitions/")


async def _alarm(fake_panel: FakePanel, entry: MockConfigEntry) -> None:
    fake_panel.partitions[1].state = "set"
    fake_panel.trigger_alarm(1, "209")
    await coordinator_of(entry).async_refresh()


async def test_disarm_during_alarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Disarming acknowledges first; unset is never sent from an alarm."""
    entry = await setup(code=CODE)
    await _alarm(fake_panel, entry)
    sent = len(fake_panel.stats.requests)
    await call_panel(hass, "alarm_disarm")
    assert state_of(hass, PANEL) == AlarmControlPanelState.DISARMED
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND, PUT, *ROUND]
    # the fake panel would record a violation for unset during an alarm


async def test_disarm_from_acknowledged(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """After acknowledging, disarming is a single command."""
    entry = await setup(code=CODE)
    await _alarm(fake_panel, entry)
    # acknowledged at the keypad or in the app
    fake_panel.partitions[1].state = "acknowledged"
    await coordinator_of(entry).async_refresh()
    sent = len(fake_panel.stats.requests)
    await call_panel(hass, "alarm_disarm")
    assert state_of(hass, PANEL) == AlarmControlPanelState.DISARMED
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND]


async def test_acknowledging_first_fails(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed acknowledgement stops disarming and is named."""
    entry = await setup(code=CODE)
    await _alarm(fake_panel, entry)
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "status", status=500))
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_disarm")
    assert err.value.translation_key == "acknowledge_first_failed_error"
    assert "acknowledging the alarm first failed" in str(err.value)
    assert state_of(hass, PANEL) == AlarmControlPanelState.TRIGGERED
    assert fake_panel.stats.requests.count(PUT) == 1


async def test_no_arming_during_an_alarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming during an alarm isn't sent at all."""
    entry = await setup(code=CODE)
    await _alarm(fake_panel, entry)
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_home",
            {"entity_id": PANEL, "code": CODE},
            blocking=True,
        )
    assert err.value.translation_key == "arm_during_alarm"
    # only the fresh read; nothing is sent
    assert fake_panel.stats.requests[sent:] == [READ]
