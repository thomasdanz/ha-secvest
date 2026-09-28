"""One alarm panel per selected partition."""

import logging
from typing import Any

from homeassistant.components.alarm_control_panel import AlarmControlPanelEntity
from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import AlarmType, PanelEvent, Partition, PartitionState
from .codes import codes, find
from .commands import async_set_partition_state
from .const import DOMAIN
from .coordinator import SecvestCoordinator
from .entity import SecvestEntity

_LOGGER = logging.getLogger(__name__)

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


def _alarm_type(alarm: PanelEvent) -> str:
    """Return the translation key of an alarm type, or the raw code."""
    if isinstance(alarm.type, AlarmType):
        return alarm.type.name.lower()
    return str(alarm.type)


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

    _attr_translation_key = "partition"
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )
    # four-digit codes like at the keypad, for arming and disarming (#116)
    _attr_code_format = CodeFormat.NUMBER
    _attr_code_arm_required = True

    def __init__(self, coordinator: SecvestCoordinator, partition: Partition) -> None:
        """Name the entity after the partition."""
        super().__init__(coordinator, f"partition_{partition.number}_alarm")
        self.number = partition.number
        self._attr_name = partition.name
        self._alarm_warned = False

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
        if partition is None:
            return None
        state = STATES.get(partition.state)
        if state is not AlarmControlPanelState.TRIGGERED and self._alarms():
            # /alarms/ reports an alarm the partition state doesn't show;
            # not observed, so it is shown as triggered and noted once
            if not self._alarm_warned:
                self._alarm_warned = True
                _LOGGER.warning(
                    "The panel reports an alarm for partition %s in state %s",
                    self.number,
                    partition.state,
                )
            return AlarmControlPanelState.TRIGGERED
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Keep the raw state and tell an acknowledged alarm apart."""
        partition = self._partition
        if partition is None:
            return {}
        alarms = self._alarms()
        return {
            "panel_state": str(partition.state),
            "acknowledged": partition.state == PartitionState.ACKNOWLEDGED,
            # the first alarm's type, translated; unknown codes stay raw
            "alarm_type": _alarm_type(alarms[0]) if alarms else None,
            "alarm_zones": [alarm.zone_id for alarm in alarms if alarm.zone_id],
        }

    def _alarms(self) -> list[PanelEvent]:
        return [
            alarm
            for alarm in self.coordinator.data.alarms
            if self.number in alarm.partitions
        ]

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the partition."""
        await self._command(code, PartitionState.UNSET)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the partition internally."""
        await self._command(code, PartitionState.PARTSET)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the partition completely."""
        await self._command(code, PartitionState.SET)

    async def _command(self, code: str | None, target: PartitionState) -> None:
        """Check the code, then send the command; nothing is sent otherwise."""
        entry = self.coordinator.config_entry
        if not codes(entry):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_codes"
            )
        user = find(entry, code)
        if user is None:
            _LOGGER.warning("Wrong code for partition %s", self.number)
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_code"
            )
        await async_set_partition_state(self.coordinator, self.number, target)
        self._attr_changed_by = user.name
        self.async_write_ha_state()
