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
            "binary_sensor.alarmanlage_teilber_1_arming_blocked",
            None,
        ),
        "problem": ("binary_sensor.alarmanlage_problem", None),
        "faults": ("sensor.alarmanlage_faults", None),
        "installer_lock": (
            "binary_sensor.alarmanlage_installer_lock",
            EntityCategory.DIAGNOSTIC,
        ),
    }
    zones = fake_panel.partitions[1].zone_ids
    expected = set(panel) | {
        f"zone_{zone_id}_{key}" for zone_id in zones for key in ("open", "problem")
    }
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
