"""Updates over an existing config entry, without setting up again (#103)."""

import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.config_flow import SecvestConfigFlow
from custom_components.secvest.const import (
    CONF_EXCLUDED_ZONES,
    CONF_PARTITIONS,
    CONF_ZONE_DEVICE_CLASSES,
    DOMAIN,
)

from .common import ROUND
from .fake_panel import FakePanel

STORED = json.loads(
    (Path(__file__).parent / "upgrade" / "stored_entries.json").read_text()
)
MANIFEST = Path(__file__).parent.parent / "custom_components/secvest/manifest.json"


def _cases() -> list[Any]:
    return [
        pytest.param(entry, id=f"{entry['name']} ({entry['versions'][0]}+)")
        for entry in STORED["entries"]
    ]


def _fill(value: Any, panel: FakePanel) -> Any:
    if isinstance(value, str):
        return value.format(
            url=panel.url, user_code=panel.user_code, password=panel.password
        )
    if isinstance(value, dict):
        return {key: _fill(item, panel) for key, item in value.items()}
    return value


def _unique_ids(stored: dict[str, Any], panel: FakePanel) -> list[str]:
    ids = [*stored["unique_ids"]["panel"]]
    for key in stored["unique_ids"]["partition_1"]:
        ids.append(f"partition_1_{key}")
    excluded = set(stored["options"].get(CONF_EXCLUDED_ZONES, []))
    for zone_id in panel.partitions[1].zone_ids:
        if zone_id not in excluded:
            ids.extend(f"zone_{zone_id}_{key}" for key in stored["unique_ids"]["zones"])
    return ids


def _domain(unique_id: str) -> str:
    """Return the platform of an entity kind, by the end of its unique id."""
    if unique_id.endswith("_alarm"):
        return "alarm_control_panel"
    if unique_id == "faults" or unique_id.endswith("_open_zones"):
        return "sensor"
    return "binary_sensor"


def test_current_version_is_covered() -> None:
    """Every release adds its stored entry to the test set."""
    version = json.loads(MANIFEST.read_text())["version"]
    assert any(version in entry["versions"] for entry in STORED["entries"])


@pytest.mark.parametrize("stored", _cases())
async def test_update_keeps_the_entry_and_its_entities(
    hass: HomeAssistant, fake_panel: FakePanel, stored: dict[str, Any]
) -> None:
    """An entry stored by an older version loads and keeps every entity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fake_panel.name,
        version=1,
        minor_version=stored["minor_version"],
        data=_fill(stored["data"], fake_panel),
        options=stored["options"],
        subentries_data=stored.get("subentries", []),
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = _unique_ids(stored, fake_panel)
    # as the older version registered them
    entity_ids = {
        unique_id: registry.async_get_or_create(
            _domain(unique_id),
            DOMAIN,
            f"{entry.entry_id}_{unique_id}",
            config_entry=entry,
        ).entity_id
        for unique_id in old
    }
    # the group sensors belong to their subentries
    for subentry in stored.get("subentries", []):
        for key in stored["unique_ids"].get("groups", []):
            unique_id = f"group_{subentry['subentry_id']}_{key}"
            entity_ids[unique_id] = registry.async_get_or_create(
                "binary_sensor",
                DOMAIN,
                f"{entry.entry_id}_{unique_id}",
                config_entry=entry,
                config_subentry_id=subentry["subentry_id"],
            ).entity_id

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (
        SecvestConfigFlow.VERSION,
        SecvestConfigFlow.MINOR_VERSION,
    )
    # the settings are kept; migrations only add defaults
    for key, value in stored["options"].items():
        assert entry.options[key] == value
    assert entry.options[CONF_PARTITIONS] == [1]
    assert CONF_EXCLUDED_ZONES in entry.options
    assert CONF_ZONE_DEVICE_CLASSES in entry.options
    # every entity keeps its unique id and entity id, and works
    for unique_id, entity_id in entity_ids.items():
        entity = registry.async_get(entity_id)
        assert entity is not None, unique_id
        assert entity.unique_id == f"{entry.entry_id}_{unique_id}"
        assert hass.states.get(entity_id) is not None, unique_id
    # nothing but a normal round was sent
    assert fake_panel.stats.requests == ROUND
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_newer_minor_version_loads(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """A compatible change of a newer version doesn't block an older one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fake_panel.name,
        version=1,
        minor_version=SecvestConfigFlow.MINOR_VERSION + 1,
        data=_fill(STORED["entries"][-1]["data"], fake_panel),
        options=STORED["entries"][-1]["options"],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_newer_major_version_is_refused(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """An entry of a newer, incompatible version isn't misread."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fake_panel.name,
        version=SecvestConfigFlow.VERSION + 1,
        data=_fill(STORED["entries"][-1]["data"], fake_panel),
        options=STORED["entries"][-1]["options"],
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert fake_panel.stats.requests == []


async def test_entities_of_removed_kinds_are_removed(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """Kinds that no longer exist go; missing zones keep their entities."""
    stored = STORED["entries"][-1]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=fake_panel.name,
        version=1,
        minor_version=SecvestConfigFlow.MINOR_VERSION,
        data=_fill(stored["data"], fake_panel),
        options=stored["options"],
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)

    def register(domain: str, key: str) -> str:
        return registry.async_get_or_create(
            domain, DOMAIN, f"{entry.entry_id}_{key}", config_entry=entry
        ).entity_id

    removed = [
        register("button", "refresh"),
        register("binary_sensor", "zone_209_battery"),
    ]
    # a zone the panel doesn't report right now
    kept = register("binary_sensor", "zone_299_open")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    for entity_id in removed:
        assert registry.async_get(entity_id) is None, entity_id
    assert registry.async_get(kept) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
