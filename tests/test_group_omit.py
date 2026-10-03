"""The omit switch of a zone group (#136)."""

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.commands import CommandError
from custom_components.secvest.const import (
    CONF_HIDE_MEMBERS,
    CONF_ZONES,
    SUBENTRY_ZONE_GROUP,
)

from .common import CODE, ROUND, Setup, call_panel, coordinator_of, get_state, state_of
from .fake_panel import FakePanel, Injection

SWITCH = "switch.alarmanlage_room_3_omit"


def _put(zone_id: str) -> tuple[str, str]:
    return ("PUT", f"/system/partitions-1/zones-{zone_id}/")


async def _add_group(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    zones: list[str],
    *,
    hide_members: bool = False,
) -> Any:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Room 3",
            CONF_ZONES: zones,
            CONF_DEVICE_CLASS: "window",
            CONF_HIDE_MEMBERS: hide_members,
        },
    )
    await hass.async_block_till_done()
    return result


async def test_omit_and_include_the_group(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """One switch omits all members, one after another, each verified."""
    entry = await setup()
    await _add_group(hass, entry, ["203", "204"])
    registry = er.async_get(hass)
    switch = registry.async_get(SWITCH)
    assert switch is not None
    (subentry_id,) = entry.subentries
    assert switch.unique_id == f"{entry.entry_id}_group_{subentry_id}_omit"
    assert switch.config_subentry_id == subentry_id
    group = registry.async_get("binary_sensor.alarmanlage_room_3")
    assert group is not None
    assert switch.device_id == group.device_id
    assert (
        get_state(hass, SWITCH).attributes["friendly_name"] == "Zone group Room 3 Omit"
    )
    assert state_of(hass, SWITCH) == STATE_OFF

    sent = len(fake_panel.stats.requests)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    assert fake_panel.stats.requests[sent:] == [
        _put("203"),
        *ROUND,
        _put("204"),
        *ROUND,
    ]
    assert state_of(hass, SWITCH) == STATE_ON
    assert get_state(hass, SWITCH).attributes["omitted_zones"] == ["203", "204"]

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    assert not fake_panel.zones["203"].omitted
    assert not fake_panel.zones["204"].omitted
    assert state_of(hass, SWITCH) == STATE_OFF


async def test_partly_omitted(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """One member omitted elsewhere: off; turning on omits only the rest."""
    entry = await setup()
    await _add_group(hass, entry, ["203", "204"])
    fake_panel.zones["203"].omitted = True
    await coordinator_of(entry).async_refresh()
    assert state_of(hass, SWITCH) == STATE_OFF
    sent = len(fake_panel.stats.requests)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    assert fake_panel.stats.requests[sent:] == [_put("204"), *ROUND]
    assert state_of(hass, SWITCH) == STATE_ON


async def test_mixed_group(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A member that can't be omitted is never sent, and is listed."""
    entry = await setup()
    await _add_group(hass, entry, ["203", "219"])
    assert get_state(hass, SWITCH).attributes["not_omittable_zones"] == ["219"]
    sent = len(fake_panel.stats.requests)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    assert fake_panel.stats.requests[sent:] == [_put("203"), *ROUND]
    assert state_of(hass, SWITCH) == STATE_ON


async def test_no_switch_without_omittable_members(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A group of entry doors gets no omit switch."""
    entry = await setup()
    await _add_group(hass, entry, ["211", "219"])
    assert hass.states.get(SWITCH) is None


async def test_failure_in_the_middle(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failure stops the sequence; changed zones stay; the rest is named."""
    entry = await setup()
    await _add_group(hass, entry, ["203", "204", "205"])
    fake_panel.inject(
        Injection("PUT", "/system/partitions-1/zones-204/", "status", status=403)
    )
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
        )
    assert err.value.translation_key == "group_omit_failed"
    placeholders = err.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["zones"] == "Room 3 R, Room 4 R"
    assert str(err.value).startswith(
        "Zone group Room 3: Room 3 R, Room 4 R not omitted."
    )
    # no rollback, and nothing sent after the failure
    assert fake_panel.zones["203"].omitted
    assert not fake_panel.zones["205"].omitted
    assert _put("205") not in fake_panel.stats.requests[sent:]
    assert state_of(hass, SWITCH) == STATE_OFF


async def test_follows_the_disarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The panel includes the zones again at disarm; the switch follows."""
    entry = await setup(code=CODE)
    await _add_group(hass, entry, ["203", "204"])
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    await call_panel(hass, "alarm_arm_away")
    assert state_of(hass, SWITCH) == STATE_ON
    await call_panel(hass, "alarm_disarm")
    assert state_of(hass, SWITCH) == STATE_OFF


async def test_kept_and_not_hidden_with_hidden_members(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Hiding the members hides their switches, not the group's; a reload keeps it."""
    entry = await setup()
    await _add_group(hass, entry, ["203", "204"], hide_members=True)
    registry = er.async_get(hass)
    member = registry.async_get("switch.alarmanlage_room_3_l_omit")
    assert member is not None
    assert member.hidden_by is er.RegistryEntryHider.INTEGRATION
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    switch = registry.async_get(SWITCH)
    assert switch is not None
    assert switch.hidden_by is None
    assert state_of(hass, SWITCH) == STATE_OFF
