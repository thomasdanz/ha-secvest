"""Problem, battery, supervision and tamper of zone groups (#200)."""

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest import web as web_module
from custom_components.secvest.const import (
    CONF_HIDE_MEMBERS,
    CONF_WEB_INTERFACE,
    CONF_ZONES,
    SUBENTRY_ZONE_GROUP,
)

from .common import Setup, coordinator_of, get_state, state_of
from .fake_panel import FakePanel, Injection

PROBLEM = "binary_sensor.alarmanlage_room_3_problem"
BATTERY = "binary_sensor.alarmanlage_room_3_battery"
SUPERVISION = "binary_sensor.alarmanlage_room_3_supervision"
TAMPER = "binary_sensor.alarmanlage_room_3_tamper"


@pytest.fixture(autouse=True)
def no_automatic_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the web interface only when a test asks for it."""
    monkeypatch.setattr(web_module, "FIRST_WEB_DELAY", 3600)
    monkeypatch.setattr(web_module, "MIN_WEB_SPACING", 0)


async def _add_group(
    hass: HomeAssistant, entry: MockConfigEntry, *, hide_members: bool = True
) -> Any:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Room 3",
            CONF_ZONES: ["203", "204"],
            CONF_DEVICE_CLASS: "window",
            CONF_HIDE_MEMBERS: hide_members,
        },
    )
    await hass.async_block_till_done()
    return result


def _fault(zone_id: str) -> dict[str, Any]:
    return {
        "type": "1234",
        "id": "1",
        "ui-string": f"Z{zone_id} fault",
        "affects-partition": ["1"],
        "affects-zone": zone_id,
        "prevents-set": False,
        "prevents-reset": False,
        "is-rf-warning": False,
    }


async def _read(entry: MockConfigEntry) -> None:
    web = coordinator_of(entry).web
    assert web is not None
    await web.async_refresh()


async def test_problem(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A member's problem shows on the group; without the web interface too."""
    entry = await setup()
    await _add_group(hass, entry)
    assert state_of(hass, PROBLEM) == STATE_OFF
    assert hass.states.get(BATTERY) is None

    fake_panel.static_faults.append(_fault("204"))
    await coordinator_of(entry).async_refresh()
    assert state_of(hass, PROBLEM) == STATE_ON
    assert get_state(hass, PROBLEM).attributes["problem_zones"] == ["204"]
    # an "open" fault is no problem
    fake_panel.static_faults.clear()
    fake_panel.open_zone("203")
    await coordinator_of(entry).async_refresh()
    assert state_of(hass, PROBLEM) == STATE_OFF

    # another zone state, e.g. tamper, is one
    fake_panel.zones["203"].state = "tamper"
    await coordinator_of(entry).async_refresh()
    assert state_of(hass, PROBLEM) == STATE_ON


async def test_entities_belong_to_the_group(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """On the group's device and subentry, not hidden with the members."""
    entry = await setup(**{CONF_WEB_INTERFACE: True})
    await _add_group(hass, entry, hide_members=True)
    (subentry_id,) = entry.subentries
    registry = er.async_get(hass)
    for entity_id, key in (
        (PROBLEM, "problem"),
        (BATTERY, "battery"),
        (SUPERVISION, "supervision"),
        (TAMPER, "tamper"),
    ):
        entity = registry.async_get(entity_id)
        assert entity is not None, entity_id
        assert entity.unique_id == f"{entry.entry_id}_group_{subentry_id}_{key}"
        assert entity.config_subentry_id == subentry_id
        assert entity.hidden_by is None
    # no signal strength for groups
    assert hass.states.get("sensor.alarmanlage_room_3_signal_strength") is None
    # the members' own web entities are hidden with them
    member = registry.async_get("binary_sensor.alarmanlage_room_3_l_battery")
    assert member is not None
    assert member.hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_web_status(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """On if a member reports it, off if all report it off, else unknown."""
    entry = await setup(**{CONF_WEB_INTERFACE: True})
    await _add_group(hass, entry)
    assert state_of(hass, BATTERY) == STATE_UNKNOWN
    await _read(entry)
    for entity_id in (BATTERY, SUPERVISION, TAMPER):
        assert state_of(hass, entity_id) == STATE_OFF
    assert get_state(hass, BATTERY).attributes["last_read"] is not None

    fake_panel.web_rows["204"].battery_low = True
    fake_panel.web_rows["203"].tamper = True
    await _read(entry)
    assert state_of(hass, BATTERY) == STATE_ON
    assert get_state(hass, BATTERY).attributes["zones"] == ["204"]
    assert state_of(hass, TAMPER) == STATE_ON
    assert state_of(hass, SUPERVISION) == STATE_OFF

    # a member missing from the web interface's list leaves "off" unknown
    fake_panel.web_rows["204"].battery_low = False
    del fake_panel.web_rows["203"]
    await _read(entry)
    assert state_of(hass, BATTERY) == STATE_UNKNOWN


async def test_members_no_longer_listed_dont_count(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A member the partitions no longer list is ignored, as by the group."""
    entry = await setup(**{CONF_WEB_INTERFACE: True})
    await _add_group(hass, entry)
    fake_panel.static_faults.append(_fault("204"))
    fake_panel.web_rows["204"].battery_low = True
    fake_panel.partitions[1].zone_ids.remove("204")
    await coordinator_of(entry).async_refresh()
    await _read(entry)
    assert state_of(hass, PROBLEM) == STATE_OFF
    assert state_of(hass, BATTERY) == STATE_OFF


async def test_web_blocked_and_turned_off(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """After a failed web login unavailable; turned off, the entities go."""
    entry = await setup(**{CONF_WEB_INTERFACE: True})
    await _add_group(hass, entry)
    fake_panel.inject(
        Injection(
            "POST",
            "/sec_login.cgi",
            "status",
            status=200,
            body=b'<input type="hidden" value="0" id="ssid" />',
            content_type="text/html",
        )
    )
    await _read(entry)
    assert state_of(hass, BATTERY) == STATE_UNAVAILABLE
    # the REST problem sensor isn't affected
    assert state_of(hass, PROBLEM) == STATE_OFF

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_WEB_INTERFACE: False}
    )
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get(BATTERY) is None
    assert registry.async_get(PROBLEM) is not None


async def test_no_member_listed(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without any listed member nothing is known about the group."""
    entry = await setup(**{CONF_WEB_INTERFACE: True})
    await _add_group(hass, entry)
    fake_panel.partitions[1].zone_ids = [
        z for z in fake_panel.partitions[1].zone_ids if z not in {"203", "204"}
    ]
    await coordinator_of(entry).async_refresh()
    await _read(entry)
    assert state_of(hass, BATTERY) == STATE_UNKNOWN
    assert state_of(hass, PROBLEM) == STATE_UNAVAILABLE
