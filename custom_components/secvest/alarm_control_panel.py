"""One alarm panel per selected partition."""

from typing import Any

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import Partition, PartitionState
from .commands import async_set_partition_state
from .coordinator import SecvestCoordinator
from .entity import SecvestEntity

PARALLEL_UPDATES = 0

STATES: dict[PartitionState | str, AlarmControlPanelState] = {
    PartitionState.UNSET: AlarmControlPanelState.DISARMED,
    PartitionState.PARTSET: AlarmControlPanelState.ARMED_HOME,
    PartitionState.SET: AlarmControlPanelState.ARMED_AWAY,
    # the firmware reports an alarm in the partition state itself
    PartitionState.UNSET_ALARM: AlarmControlPanelState.TRIGGERED,
    PartitionState.PARTSET_ALARM: AlarmControlPanelState.TRIGGERED,
    PartitionState.SET_ALARM: AlarmControlPanelState.TRIGGERED,
    PartitionState.ACKNOWLEDGED: AlarmControlPanelState.TRIGGERED,
    # in the official app's enum; the firmware isn't known to send it
    "alarm": AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a panel for each selected partition the panel reports."""
    coordinator = entry.runtime_data
    partitions = coordinator.data.partitions
    async_add_entities(
        SecvestAlarmPanel(coordinator, partitions[number])
        for number in coordinator.selected_partitions
        if number in partitions
    )


class SecvestAlarmPanel(SecvestEntity, AlarmControlPanelEntity):
    """A partition: its state, arming and disarming.

    Every command is verified by the state read afterwards; a failure
    raises one error type and fires the arming_failed event.
    """

    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    # the panel's credentials are the authorization
    _attr_code_arm_required = False

    def __init__(self, coordinator: SecvestCoordinator, partition: Partition) -> None:
        """Name the entity after the partition."""
        super().__init__(coordinator, f"partition_{partition.number}_alarm")
        self.number = partition.number
        self._attr_name = partition.name

    @property
    def _partition(self) -> Partition | None:
        return self.coordinator.data.partitions.get(self.number)

    @property
    def available(self) -> bool:
        """Unavailable while the panel doesn't report the partition."""
        return super().available and self._partition is not None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Map the partition state; unknown values give an unknown state."""
        partition = self._partition
        return None if partition is None else STATES.get(partition.state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Keep the raw state and tell an acknowledged alarm apart."""
        partition = self._partition
        if partition is None:
            return {}
        return {
            "panel_state": str(partition.state),
            "acknowledged": partition.state == PartitionState.ACKNOWLEDGED,
        }

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the partition."""
        await async_set_partition_state(
            self.coordinator, self.number, PartitionState.UNSET
        )

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the partition internally."""
        await async_set_partition_state(
            self.coordinator, self.number, PartitionState.PARTSET
        )

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the partition completely."""
        await async_set_partition_state(
            self.coordinator, self.number, PartitionState.SET
        )
