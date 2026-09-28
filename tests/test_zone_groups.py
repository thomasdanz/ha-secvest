"""Tests for zone groups, configured as subentries (#67)."""

from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    CONF_DEVICE_CLASS,
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.const import (
    CONF_AUTH_FAILED,
    CONF_HIDE_MEMBERS,
    CONF_ZONES,
    DOMAIN,
    SUBENTRY_ZONE_GROUP,
)

from .common import Setup, coordinator_of
from .fake_panel import FakePanel

GROUP = "binary_sensor.alarmanlage_room_3"
MEMBERS = ("binary_sensor.alarmanlage_room_3_l", "binary_sensor.alarmanlage_room_3_r")


def _group(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_NAME: "Room 3",
        CONF_ZONES: ["203", "204"],
        CONF_DEVICE_CLASS: "window",
        CONF_HIDE_MEMBERS: True,
    }
    data.update(changes)
    return data


async def _add(
    hass: HomeAssistant, entry: MockConfigEntry, data: dict[str, Any]
) -> Any:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], data
    )
    await hass.async_block_till_done()
    return result


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, subentry_id: str, data: dict[str, Any]
) -> Any:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], data
    )
    await hass.async_block_till_done()
    return result


def _subentry_id(entry: MockConfigEntry) -> str:
    (subentry_id,) = entry.subentries
    return str(subentry_id)


def _hidden(hass: HomeAssistant, entity_id: str) -> er.RegistryEntryHider | None:
    entity = er.async_get(hass).async_get(entity_id)
    assert entity is not None
    return entity.hidden_by


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_add_group(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group gets its own device and sensor; the zones keep theirs."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 30)
    entry = await setup()
    sent = len(fake_panel.stats.requests)
    result = await _add(hass, entry, _group())
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # the reload takes the last round: loaded at once, nothing sent
    assert entry.state is ConfigEntryState.LOADED
    assert len(fake_panel.stats.requests) == sent
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.05)

    state = hass.states.get(GROUP)
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["friendly_name"] == "Zone group Room 3"
    assert state.attributes[ATTR_DEVICE_CLASS] == "window"
    assert state.attributes["zones"] == ["203", "204"]

    registry = er.async_get(hass)
    entity = registry.async_get(GROUP)
    assert entity is not None
    subentry_id = _subentry_id(entry)
    assert entity.unique_id == f"{entry.entry_id}_group_{subentry_id}_open"
    assert entity.config_subentry_id == subentry_id
    devices = dr.async_get(hass)
    device = devices.async_get(entity.device_id)  # type: ignore[arg-type]
    assert isinstance(device, dr.DeviceEntry)
    assert device.name == "Zone group Room 3"
    assert device.model == "Zone group"
    # a Home Assistant concept, not an ABUS device
    assert device.manufacturer is None
    panel = coordinator_of(entry).panel_device_id
    assert device.via_device_id == panel

    # the member zones keep their own devices and entity ids, but are hidden
    for member in MEMBERS:
        zone = registry.async_get(member)
        assert zone is not None
        assert zone.device_id != entity.device_id
        assert zone.hidden_by is er.RegistryEntryHider.INTEGRATION

    fake_panel.open_zone("204")
    await coordinator_of(entry).async_refresh()
    assert _state(hass, GROUP) == STATE_ON
    attributes = hass.states.get(GROUP).attributes  # type: ignore[union-attr]
    assert attributes["open_zones"] == ["204"]


async def test_group_states(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unknown while a member is in another state and none is open."""
    entry = await setup()
    await _add(hass, entry, _group())
    fake_panel.zones["203"].state = "tamper"
    await coordinator_of(entry).async_refresh()
    assert _state(hass, GROUP) == "unknown"
    fake_panel.open_zone("204")
    await coordinator_of(entry).async_refresh()
    assert _state(hass, GROUP) == STATE_ON


async def test_invalid_input(hass: HomeAssistant, setup: Setup) -> None:
    """Name and at least two zones are required; names are unique."""
    entry = await setup()
    result = await _add(hass, entry, _group(**{CONF_ZONES: ["203"]}))
    assert result["errors"] == {CONF_ZONES: "too_few_zones"}
    result = await _add(hass, entry, _group(**{CONF_NAME: " "}))
    assert result["errors"] == {CONF_NAME: "name_required"}
    await _add(hass, entry, _group())
    result = await _add(
        hass, entry, _group(**{CONF_NAME: "room 3", CONF_ZONES: ["205", "206"]})
    )
    assert result["errors"] == {CONF_NAME: "name_exists"}


async def test_zone_in_one_group_only(hass: HomeAssistant, setup: Setup) -> None:
    """Zones of another group aren't offered."""
    entry = await setup()
    await _add(hass, entry, _group())
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    schema = result["data_schema"]
    assert schema is not None
    offered = {
        option["value"]
        for key, selector in schema.schema.items()
        if key == CONF_ZONES
        for option in selector.config["options"]
    }
    assert offered
    assert not offered & {"203", "204"}


async def test_reconfigure(hass: HomeAssistant, setup: Setup) -> None:
    """Changes reload; members leaving or unhidden are shown again."""
    entry = await setup()
    await _add(hass, entry, _group())
    subentry_id = _subentry_id(entry)
    result = await _reconfigure(
        hass,
        entry,
        subentry_id,
        _group(**{CONF_NAME: "Room 3 new", CONF_ZONES: ["203", "205"]}),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    # the entity id stays, the name follows
    assert _state(hass, GROUP) == STATE_OFF
    assert (
        hass.states.get(GROUP).attributes["friendly_name"]  # type: ignore[union-attr]
        == "Zone group Room 3 new"
    )
    assert _hidden(hass, MEMBERS[1]) is None
    assert _hidden(hass, "binary_sensor.alarmanlage_room_4_r") is not None

    await _reconfigure(
        hass,
        entry,
        subentry_id,
        _group(**{CONF_ZONES: ["203", "205"]}, hide_members=False),
    )
    assert _hidden(hass, MEMBERS[0]) is None


async def test_user_hidden_entity_stays_hidden(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Only what the integration hid is shown again."""
    entry = await setup()
    registry = er.async_get(hass)
    registry.async_update_entity(MEMBERS[0], hidden_by=er.RegistryEntryHider.USER)
    await _add(hass, entry, _group(hide_members=False))
    assert _hidden(hass, MEMBERS[0]) is er.RegistryEntryHider.USER


async def test_delete_group(hass: HomeAssistant, setup: Setup) -> None:
    """Deleting the group removes its device and sensor, and shows the zones."""
    entry = await setup()
    await _add(hass, entry, _group())
    registry = er.async_get(hass)
    entity = registry.async_get(GROUP)
    assert entity is not None
    device_id = entity.device_id
    assert hass.config_entries.async_remove_subentry(entry, _subentry_id(entry))
    await hass.async_block_till_done()
    assert registry.async_get(GROUP) is None
    assert hass.states.get(GROUP) is None
    assert dr.async_get(hass).async_get(device_id) is None  # type: ignore[arg-type]
    for member in MEMBERS:
        assert _hidden(hass, member) is None


async def test_not_loaded(hass: HomeAssistant, setup: Setup) -> None:
    """Without a loaded entry there are no zones to choose from."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_group_survives_a_restart(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A reload keeps the group sensor and its entity id."""
    entry = await setup()
    await _add(hass, entry, _group())
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, GROUP) == STATE_OFF


def _group_issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"group_{entry.entry_id}_{_subentry_id(entry)}"
    )


async def test_member_zone_gone(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A zone the partitions no longer list is ignored and reported."""
    entry = await setup()
    await _add(hass, entry, _group())
    assert _group_issue(hass, entry) is None
    fake_panel.partitions[1].zone_ids.remove("204")
    await coordinator_of(entry).async_refresh()

    issue = _group_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "zone_group_zones_gone"
    assert issue.translation_placeholders == {
        "group": "Room 3",
        "zones": "204",
        "name": "Alarmanlage",
    }
    # the remaining member decides
    assert _state(hass, GROUP) == STATE_OFF
    attributes = hass.states.get(GROUP).attributes  # type: ignore[union-attr]
    assert attributes["missing_zones"] == ["204"]
    fake_panel.open_zone("203")
    await coordinator_of(entry).async_refresh()
    assert _state(hass, GROUP) == STATE_ON

    # listed again: the issue goes away
    fake_panel.partitions[1].zone_ids.append("204")
    await coordinator_of(entry).async_refresh()
    assert _group_issue(hass, entry) is None


async def test_all_member_zones_gone(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without any listed member the group sensor is unavailable."""
    entry = await setup()
    await _add(hass, entry, _group())
    fake_panel.partitions[1].zone_ids.remove("203")
    fake_panel.partitions[1].zone_ids.remove("204")
    await coordinator_of(entry).async_refresh()
    assert _state(hass, GROUP) == "unavailable"
    assert _group_issue(hass, entry) is not None


async def test_issue_removed_with_the_group(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Deleting the group removes its issue."""
    entry = await setup()
    await _add(hass, entry, _group())
    fake_panel.partitions[1].zone_ids.remove("204")
    await coordinator_of(entry).async_refresh()
    assert _group_issue(hass, entry) is not None
    subentry_id = _subentry_id(entry)
    assert hass.config_entries.async_remove_subentry(entry, subentry_id)
    await hass.async_block_till_done()
    assert [i for d, i in ir.async_get(hass).issues if d == DOMAIN] == []


async def test_no_zone_preselected(hass: HomeAssistant, setup: Setup) -> None:
    """The zones field has no default, so the frontend selects nothing."""
    entry = await setup()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    schema = result["data_schema"]
    assert schema is not None
    (key,) = (k for k in schema.schema if k == CONF_ZONES)
    assert isinstance(key, vol.Optional)
    assert key.default is vol.UNDEFINED
    suggested = {
        str(k): (k.description or {}).get("suggested_value") for k in schema.schema
    }
    assert suggested[CONF_ZONES] is None
    assert suggested[CONF_DEVICE_CLASS] == "same_as_zones"


async def test_same_as_zones(hass: HomeAssistant, setup: Setup) -> None:
    """The group shows what its zones show; mixed types need a choice."""
    entry = await setup(
        zone_device_classes={"203": "window", "204": "window", "205": "door"}
    )
    result = await _add(
        hass,
        entry,
        _group(**{CONF_ZONES: ["203", "205"], CONF_DEVICE_CLASS: "same_as_zones"}),
    )
    assert result["errors"] == {CONF_DEVICE_CLASS: "zones_differ"}
    result = await _add(hass, entry, _group(**{CONF_DEVICE_CLASS: "same_as_zones"}))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    state = hass.states.get(GROUP)
    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == "window"


async def test_same_as_zones_follows_show_as(hass: HomeAssistant, setup: Setup) -> None:
    """The user's "Show as" on the zones counts."""
    entry = await setup()
    registry = er.async_get(hass)
    for member in MEMBERS:
        registry.async_update_entity(member, device_class="door")
    await _add(hass, entry, _group(**{CONF_DEVICE_CLASS: "same_as_zones"}))
    state = hass.states.get(GROUP)
    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == "door"


async def test_next_group_right_away(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After adding a group, the next one can be added at once."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 30)
    entry = await setup()
    await _add(hass, entry, _group())
    result = await _add(
        hass, entry, _group(**{CONF_NAME: "Room 4", CONF_ZONES: ["205", "206"]})
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.states.get("binary_sensor.alarmanlage_room_4") is not None


def _group_device(hass: HomeAssistant) -> dr.DeviceEntry:
    entity = er.async_get(hass).async_get(GROUP)
    assert entity is not None
    device = dr.async_get(hass).async_get(entity.device_id)  # type: ignore[arg-type]
    assert isinstance(device, dr.DeviceEntry)
    return device


async def test_area(hass: HomeAssistant, setup: Setup) -> None:
    """A new group device gets the area; reconfiguring shows and sets it."""
    areas = ar.async_get(hass)
    first = areas.async_create("First floor")
    second = areas.async_create("Second floor")
    entry = await setup()
    await _add(hass, entry, _group(area_id=first.id))
    assert _group_device(hass).area_id == first.id

    # changed on the device page: the reconfigure form shows that
    dr.async_get(hass).async_update_device(_group_device(hass).id, area_id=second.id)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": _subentry_id(entry)},
    )
    schema = result["data_schema"]
    assert schema is not None
    suggested = {
        str(k): (k.description or {}).get("suggested_value") for k in schema.schema
    }
    assert suggested["area_id"] == second.id

    # and saving the form sets it
    await _reconfigure(hass, entry, _subentry_id(entry), _group(area_id=first.id))
    assert _group_device(hass).area_id == first.id


async def test_no_area(hass: HomeAssistant, setup: Setup) -> None:
    """Without an area the device has none."""
    entry = await setup()
    await _add(hass, entry, _group())
    assert _group_device(hass).area_id is None


async def test_hidden_members_include_the_omit_switch(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Hiding grouped zones hides their omit switches too."""
    entry = await setup()
    await _add(hass, entry, _group())
    for member in MEMBERS:
        assert _hidden(hass, member.replace("binary_sensor.", "switch.") + "_omit") is (
            er.RegistryEntryHider.INTEGRATION
        )
