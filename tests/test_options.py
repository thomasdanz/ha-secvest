"""Tests for the options flow (#40)."""

from datetime import timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.const import (
    CONF_ADVANCED,
    CONF_AUTH_FAILED,
    CONF_EXCLUDED_ZONES,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    CONF_USER_AGENT,
    CONF_ZONE_DEVICE_CLASSES,
    DOMAIN,
)

from .common import Setup, coordinator_of
from .fake_panel import FakePanel


def _init_input(
    partitions: list[str], interval: int = 30, user_agent: str = ""
) -> dict[str, Any]:
    return {
        CONF_PARTITIONS: partitions,
        CONF_SCAN_INTERVAL: interval,
        CONF_ADVANCED: {CONF_USER_AGENT: user_agent},
    }


def _schema(result: Any) -> Any:
    schema = result["data_schema"]
    assert schema is not None
    return schema.schema


def _zone_devices(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    return {
        identifier.removeprefix(f"{entry.entry_id}_zone_")
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
        for _, identifier in device.identifiers
        if "_zone_" in identifier
    }


async def test_change_options(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Partitions, interval, zones and User-Agent change; the entry reloads."""
    entry = await setup()
    sent = len(fake_panel.stats.requests)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(["1", "2"], 60, " Proxy/2 ")
    )
    assert result["step_id"] == "zones"
    # nothing is sent while the options are changed
    assert len(fake_panel.stats.requests) == sent
    fields = {str(key): key for key in _schema(result)}
    assert "Room 6 L (209)" in fields
    assert fields["Room 6 L (209)"].default() == "none"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_EXCLUDED_ZONES: ["201"], "Room 6 L (209)": "door"},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_PARTITIONS: [1, 2],
        CONF_SCAN_INTERVAL: 60,
        CONF_EXCLUDED_ZONES: ["201"],
        CONF_ZONE_DEVICE_CLASSES: {"209": "door"},
    }
    assert entry.data[CONF_USER_AGENT] == "Proxy/2"
    coordinator = coordinator_of(entry)
    assert coordinator.update_interval == timedelta(seconds=60)
    assert coordinator.selected_partitions == (1, 2)
    assert fake_panel.stats.user_agents[-1] == "Proxy/2"
    state = hass.states.get("binary_sensor.room_6_l")
    assert state is not None
    assert state.attributes[ATTR_DEVICE_CLASS] == BinarySensorDeviceClass.DOOR
    # the excluded zone lost its entities and its device
    assert hass.states.get("binary_sensor.room_1") is None
    assert "201" not in _zone_devices(hass, entry)
    assert "209" in _zone_devices(hass, entry)


async def test_form_shows_the_current_options(
    hass: HomeAssistant, setup: Setup
) -> None:
    """The forms are prefilled with the current settings."""
    entry = await setup(
        **{
            CONF_SCAN_INTERVAL: 45,
            CONF_EXCLUDED_ZONES: ["201"],
            CONF_ZONE_DEVICE_CLASSES: {"209": "window"},
        }
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    suggested = {
        str(key): (key.description or {}).get("suggested_value")
        for key in _schema(result)
    }
    assert suggested[CONF_PARTITIONS] == ["1"]
    assert suggested[CONF_SCAN_INTERVAL] == 45
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(["1"], 45)
    )
    fields = {str(key): key for key in _schema(result)}
    assert fields[CONF_EXCLUDED_ZONES].default() == ["201"]
    assert fields["Room 6 L (209)"].default() == "window"


async def test_user_agent_reset(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Clearing the override goes back to ha-secvest/<version>."""
    entry = await setup(data={CONF_USER_AGENT: "Proxy/1"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(["1"], user_agent="")
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert entry.data[CONF_USER_AGENT] == ""
    assert fake_panel.stats.user_agents[0] == "Proxy/1"
    assert fake_panel.stats.user_agents[-1].startswith("ha-secvest/")  # type: ignore[union-attr]


async def test_no_partition(hass: HomeAssistant, setup: Setup) -> None:
    """At least one partition has to stay selected."""
    entry = await setup()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input([])
    )
    assert result["errors"] == {"base": "no_partitions"}


async def test_zone_settings_of_hidden_zones_are_kept(
    hass: HomeAssistant, setup: Setup
) -> None:
    """Deselecting a partition keeps its zones' device classes."""
    entry = await setup(**{CONF_ZONE_DEVICE_CLASSES: {"209": "door"}})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # partition 2 has no zones, so the zones step is skipped
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(["2"])
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_ZONE_DEVICE_CLASSES] == {"209": "door"}
    # the zones of partition 1 lost their devices
    assert _zone_devices(hass, entry) == set()


async def test_not_loaded(hass: HomeAssistant, setup: Setup) -> None:
    """Without a loaded entry there is nothing to choose from."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_missing_partition_issue(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A selected partition the panel doesn't report raises a repair issue."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    issue_id = f"missing_partition_{entry.entry_id}_2"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    partition = fake_panel.partitions.pop(2)
    await coordinator_of(entry).async_refresh()
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"partition": "2", "name": "Alarmanlage"}
    state = hass.states.get("alarm_control_panel.alarmanlage_teilber_2")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    # the issue survives a reload while the partition is still missing
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None

    # and disappears when the partition is back
    fake_panel.partitions[2] = partition
    await coordinator_of(entry).async_refresh()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_missing_partition_deselected(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Deselecting the missing partition in the options removes the issue."""
    del fake_panel.partitions[2]
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    issue_id = f"missing_partition_{entry.entry_id}_2"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id) is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _init_input(["1"])
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_issue_removed_with_the_entry(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Removing the entry removes its issues."""
    del fake_panel.partitions[2]
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    issues = [
        issue_id for domain, issue_id in ir.async_get(hass).issues if domain == DOMAIN
    ]
    assert issues == []
