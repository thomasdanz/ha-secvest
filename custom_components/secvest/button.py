"""Buttons: acknowledging an alarm per partition."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import Partition
from .commands import IN_ALARM, async_acknowledge
from .coordinator import SecvestCoordinator
from .entity import SecvestEntity

# commands wait for the request queue themselves
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an acknowledge button per selected partition."""
    coordinator = entry.runtime_data
    partitions = coordinator.data.partitions
    async_add_entities(
        AcknowledgeButton(coordinator, partitions[number])
        for number in coordinator.selected_partitions
        if number in partitions
    )


class AcknowledgeButton(SecvestEntity, ButtonEntity):
    """Acknowledge the partition's alarm; available only during an alarm.

    Like the official app: not once the alarm is acknowledged. Resetting the
    panel after an alarm isn't possible through the API.
    """

    _attr_translation_key = "acknowledge"

    def __init__(self, coordinator: SecvestCoordinator, partition: Partition) -> None:
        """Name the button after the partition."""
        super().__init__(coordinator, f"partition_{partition.number}_acknowledge")
        self.number = partition.number
        self._attr_translation_placeholders = {"partition": partition.name}

    @property
    def available(self) -> bool:
        """Available only while the partition is in an alarm state."""
        partition = self.coordinator.data.partitions.get(self.number)
        return (
            super().available and partition is not None and partition.state in IN_ALARM
        )

    async def async_press(self) -> None:
        """Acknowledge the alarm, verified."""
        await async_acknowledge(self.coordinator, self.number)
