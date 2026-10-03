"""The entities of a panel, as a whole: ids must stay stable across releases."""

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .common import Setup
from .fake_panel import FakePanel


async def test_entity_registry(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unique ids, entity ids and categories of a default setup.

    A changed unique id orphans the user's entities, so every change here
    needs a migration.
    """
    entry = await setup()
    entities = {
        entity.unique_id.removeprefix(f"{entry.entry_id}_"): entity
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    panel = {
        "partition_1_alarm": ("alarm_control_panel.alarmanlage_teilber_1", None),
        "partition_1_arming_blocked": (
            "binary_sensor.alarmanlage_teilber_1_arming",
            None,
        ),
        "partition_1_open_zones": ("sensor.alarmanlage_teilber_1_open_zones", None),
        "problem": ("binary_sensor.alarmanlage_problem", None),
        "faults": ("sensor.alarmanlage_faults", None),
        "installer_lock": (
            "binary_sensor.alarmanlage_installer_lock",
            EntityCategory.DIAGNOSTIC,
        ),
    }
    zones = fake_panel.partitions[1].zone_ids
    expected = (
        set(panel)
        | {f"zone_{zone_id}_{key}" for zone_id in zones for key in ("open", "problem")}
        # omittable zones only
        | {
            f"zone_{zone_id}_omit"
            for zone_id in zones
            if fake_panel.zones[zone_id].omittable
        }
    )
    assert set(entities) == expected
    for key, (entity_id, category) in panel.items():
        assert (entities[key].entity_id, entities[key].entity_category) == (
            entity_id,
            category,
        )
    for zone_id in zones:
        assert entities[f"zone_{zone_id}_open"].entity_category is None
        assert (
            entities[f"zone_{zone_id}_problem"].entity_category
            is EntityCategory.DIAGNOSTIC
        )


async def test_entity_ids_follow_installation_and_names(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """<domain>.<installation>_<partition or zone>, slugified."""
    # the setup fixture titles the entry like the config flow: system name
    fake_panel.name = "Butterkeks"
    fake_panel.partitions[1].name = "Krümelmonster"
    fake_panel.zones["209"].name = "Flügeltür"
    entry = await setup()
    ids = {
        entity.unique_id.removeprefix(f"{entry.entry_id}_"): entity.entity_id
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
    }
    assert ids["partition_1_alarm"] == "alarm_control_panel.butterkeks_krumelmonster"
    assert ids["partition_1_arming_blocked"] == (
        "binary_sensor.butterkeks_krumelmonster_arming"
    )
    assert ids["faults"] == "sensor.butterkeks_faults"
    assert ids["zone_209_open"] == "binary_sensor.butterkeks_flugeltur"
    assert ids["zone_209_problem"] == "binary_sensor.butterkeks_flugeltur_problem"
