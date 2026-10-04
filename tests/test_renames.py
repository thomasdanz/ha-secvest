"""Names follow renames at the panel; ids never change (#137)."""

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.const import CONF_AUTH_FAILED, DOMAIN

from .common import CODE, PANEL, ROUND, Setup, call_panel, coordinator_of, get_state
from .fake_panel import FakePanel, Injection

ZONE = "binary_sensor.alarmanlage_room_6_l"
OPEN_ZONES = "sensor.alarmanlage_teilber_1_open_zones"
FAULTS = "sensor.alarmanlage_faults"


def _zone_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_zone_209"), entry.entry_id
    )
    assert device is not None
    return device


def _panel_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )
    assert device is not None
    return device


def _ids(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    registry = er.async_get(hass)
    return {
        e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def _round(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await coordinator_of(entry).async_refresh()
    await hass.async_block_till_done()


async def test_partition_and_zone_renamed(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New names within one round, from that round: nothing more is sent."""
    # long enough that the reload right after the round takes its result
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.5)
    entry = await setup()
    ids = _ids(hass, entry)
    assert get_state(hass, PANEL).name == "Alarmanlage Teilber. 1"
    assert _zone_device(hass, entry).name == "Wireless zone Room 6 L"
    fake_panel.partitions[1].name = "House"
    fake_panel.zones["209"].name = "Front door"
    sent = len(fake_panel.stats.requests)
    await _round(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert get_state(hass, PANEL).name == "Alarmanlage House"
    assert get_state(hass, OPEN_ZONES).name == "Alarmanlage House open zones"
    assert _zone_device(hass, entry).name == "Wireless zone Front door"
    assert get_state(hass, ZONE).name == "Wireless zone Front door"
    # the reload took the round's result
    assert fake_panel.stats.requests[sent:] == ROUND
    # ids stay
    assert _ids(hass, entry) == ids


async def test_swapped_names(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matched by zone id, never by name: swapped names are renames."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.5)
    entry = await setup()
    first, second = fake_panel.zones["201"], fake_panel.zones["209"]
    first.name, second.name = second.name, first.name
    await _round(hass, entry)
    assert _zone_device(hass, entry).name == f"Wireless zone {second.name}"


async def test_no_reload_without_rename(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unchanged names and other changes don't reload."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.zones["209"].state = "open"
    await _round(hass, entry)
    assert entry.runtime_data is coordinator


async def test_names_set_in_home_assistant_stay(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A name the user gave an entity or a device is kept."""
    entry = await setup()
    er.async_get(hass).async_update_entity(OPEN_ZONES, name="My open zones")
    dr.async_get(hass).async_update_device(
        _zone_device(hass, entry).id, name_by_user="My door"
    )
    await hass.async_block_till_done()
    fake_panel.partitions[1].name = "House"
    fake_panel.zones["209"].name = "Front door"
    await _round(hass, entry)
    assert get_state(hass, OPEN_ZONES).name == "My open zones"
    assert get_state(hass, PANEL).name == "Alarmanlage House"
    device = _zone_device(hass, entry)
    assert (device.name, device.name_by_user) == ("Wireless zone Front door", "My door")
    assert get_state(hass, ZONE).name == "My door"


async def test_no_reload_from_a_command(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A command's verification never reloads; the next round does."""
    entry = await setup(code=CODE)
    coordinator = coordinator_of(entry)
    fake_panel.partitions[1].name = "House"
    await call_panel(hass, "alarm_arm_away")
    await hass.async_block_till_done()
    assert entry.runtime_data is coordinator
    await _round(hass, entry)
    assert entry.runtime_data is not coordinator
    assert get_state(hass, PANEL).name == "Alarmanlage House"


# the installation's name, on request in the options


async def _names(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "names" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "names"}
    )
    if result["type"] is FlowResultType.ABORT:
        return result
    assert result["step_id"] == "names"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    return result


async def test_installation_name_on_request(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """One GET /system/; the panel device follows, the entry's title stays."""
    entry = await setup()
    ids = _ids(hass, entry)
    fake_panel.name = "Butterkeks"
    sent = len(fake_panel.stats.requests)
    result = await _names(hass, entry)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # one GET /system/; the reload then reads a round unless the last one is
    # recent enough to be taken
    new = fake_panel.stats.requests[sent:]
    assert new[0] == ("GET", "/system/")
    assert new[1:] in ([], ROUND)
    assert _panel_device(hass, entry).name == "Butterkeks"
    assert get_state(hass, FAULTS).name == "Butterkeks Faults"
    assert entry.title == "Alarmanlage"
    assert _ids(hass, entry) == ids
    # kept across a restart
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _panel_device(hass, entry).name == "Butterkeks"


async def test_installation_name_while_locked(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The installer lock is shown and nothing changes."""
    entry = await setup()
    fake_panel.name = "Butterkeks"
    fake_panel.installer_locked = True
    result = await _names(hass, entry)
    assert result["errors"] == {"base": "installer_locked"}
    assert _panel_device(hass, entry).name == "Alarmanlage"


async def test_installation_name_unreachable(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed read changes nothing."""
    entry = await setup()
    fake_panel.inject(Injection("GET", "/system/", "status", status=500))
    result = await _names(hass, entry)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_installation_name_rejected(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 starts the reauthentication, as in a round."""
    entry = await setup()
    fake_panel.password = "changed"
    result = await _names(hass, entry)
    assert result["reason"] == "auth_failed"
    assert entry.data[CONF_AUTH_FAILED] is True
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_installation_name_not_loaded(hass: HomeAssistant, setup: Setup) -> None:
    """Without a loaded entry nothing is sent."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    result = await _names(hass, entry)
    assert result["reason"] == "not_loaded"
