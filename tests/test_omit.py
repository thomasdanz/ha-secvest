"""Tests for omitting and including zones (#27)."""

import json

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.secvest.commands import CommandError, async_set_omitted

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel, Injection

SWITCH = "switch.alarmanlage_room_6_l_omit"
PUT = ("PUT", "/system/partitions-1/zones-209/")


async def _switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {"entity_id": SWITCH}, blocking=True
    )


def _state(hass: HomeAssistant) -> str:
    state = hass.states.get(SWITCH)
    assert state is not None
    return state.state


async def test_omit_and_include(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The switch omits the zone and includes it again, verified."""
    await setup()
    assert _state(hass) == STATE_OFF
    await _switch(hass, "turn_on")
    assert _state(hass) == STATE_ON
    assert fake_panel.zones["209"].omitted
    assert fake_panel.stats.requests[len(ROUND) :] == [PUT, *ROUND]
    await _switch(hass, "turn_off")
    assert _state(hass) == STATE_OFF
    assert not fake_panel.zones["209"].omitted


async def test_only_omittable_zones(hass: HomeAssistant, setup: Setup) -> None:
    """Zones that can't be omitted get no switch."""
    entry = await setup()
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "switch", "secvest", f"{entry.entry_id}_zone_219_omit"
        )
        is None
    )
    assert registry.async_get(SWITCH) is not None


async def test_panel_includes_at_disarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The switch follows the panel's own reset of the omission."""
    entry = await setup()
    await _switch(hass, "turn_on")
    fake_panel.zones["209"].omitted = False
    await coordinator_of(entry).async_refresh()
    assert _state(hass) == STATE_OFF


async def test_no_permission(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An empty 403 for an omittable zone means no permission."""
    await setup()
    fake_panel.rights = {2}
    with pytest.raises(CommandError) as err:
        await _switch(hass, "turn_on")
    assert err.value.translation_key == "omit_failed_no_permission"
    assert str(err.value) == (
        "Zone Room 6 L was not omitted: no permission to omit zones of this partition"
    )
    assert _state(hass) == STATE_OFF


async def test_not_omittable_anymore(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If the fresh zone isn't omittable, the message says so."""
    await setup()
    # the installer changed the zone since setup
    fake_panel.zones["209"].omittable = False
    with pytest.raises(CommandError) as err:
        await _switch(hass, "turn_on")
    assert err.value.translation_key == "omit_failed_not_omittable"


async def test_error_answer(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Another error answer is reported as such after the verification."""
    await setup()
    fake_panel.inject(
        Injection("PUT", "/system/partitions-1/zones-209/", "status", status=500)
    )
    with pytest.raises(CommandError) as err:
        await _switch(hass, "turn_on")
    assert err.value.translation_key == "omit_failed_error"


async def test_ignored(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 200 without effect has no known reason."""
    await setup()
    fake_panel.inject(
        Injection(
            "PUT",
            "/system/partitions-1/zones-209/",
            "status",
            status=200,
            # the zone unchanged, as a panel that ignores the command answers
            body=json.dumps(fake_panel.zones["209"].to_json()).encode(),
            content_type="application/json",
        )
    )
    with pytest.raises(CommandError) as err:
        await _switch(hass, "turn_on")
    assert err.value.translation_key == "omit_failed_unknown"


async def test_installer_lock(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The installer lock has its own message, as for arming."""
    await setup()
    fake_panel.installer_locked = True
    with pytest.raises(CommandError) as err:
        await _switch(hass, "turn_on")
    assert err.value.translation_key == "installer_locked"
    assert not fake_panel.zones["209"].omitted


async def test_zone_no_longer_listed(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A zone no selected partition lists anymore isn't sent at all."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.partitions[1].zone_ids.remove("209")
    await coordinator.async_refresh()
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await async_set_omitted(coordinator, "209", True)
    assert err.value.translation_key == "omit_failed_unknown"
    assert len(fake_panel.stats.requests) == sent


async def test_switch_off_right_after_disarming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Disarming includes omitted zones; the verification round shows it."""
    await setup(code="4711")
    await _switch(hass, "turn_on")
    for service in ("alarm_arm_away", "alarm_disarm"):
        await hass.services.async_call(
            "alarm_control_panel",
            service,
            {"entity_id": "alarm_control_panel.alarmanlage_teilber_1", "code": "4711"},
            blocking=True,
        )
    # no polling round in between: the command's own verification did it
    assert not fake_panel.zones["209"].omitted
    assert _state(hass) == STATE_OFF
