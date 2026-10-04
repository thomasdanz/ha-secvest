"""Sensors on the panel device."""

from collections.abc import Callable
from dataclasses import dataclass
import math
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import PanelEvent, Partition
from .coordinator import SecvestCoordinator
from .entity import SecvestEntity

# the entities only read the coordinator's state
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of a panel."""
    coordinator = entry.runtime_data
    partitions = coordinator.data.partitions
    entities: list[SensorEntity] = [FaultsSensor(coordinator, "faults")]
    entities.extend(
        DiagnosticSensor(coordinator, description) for description in DIAGNOSTICS
    )
    entities.extend(
        OpenZonesSensor(coordinator, partitions[number])
        for number in coordinator.selected_partitions
        if number in partitions
    )
    async_add_entities(entities)


def _fault(fault: PanelEvent) -> dict[str, Any]:
    return {
        "type": str(fault.type),
        "id": fault.id,
        "text": fault.text,
        "partitions": list(fault.partitions),
        "zone": fault.zone_id,
        "prevents_set": fault.prevents_set,
        "prevents_reset": fault.prevents_reset,
        "is_rf_warning": fault.is_rf_warning,
    }


def _line(fault: PanelEvent) -> str:
    # the panel's text is only displayed; faults are told apart by type and id
    return fault.text or f"Fault {fault.type}/{fault.id}"


class FaultsSensor(SecvestEntity, SensorEntity):
    """The number of current faults, with all of them as attributes.

    One place for all faults, including those of components the API doesn't
    list as objects (e.g. a repeater's battery); no entity per fault. Open
    zones, which the panel lists as faults too, are counted by the open zones
    sensor instead; the problem sensor is on while this is above 0.
    """

    _attr_translation_key = "faults"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of current faults."""
        return len(self.coordinator.data.problems)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the faults and a readable summary, one line per fault."""
        faults = self.coordinator.data.problems
        return {
            "faults": [_fault(fault) for fault in faults],
            "summary": "\n".join(_line(fault) for fault in faults),
        }


class OpenZonesSensor(SecvestEntity, SensorEntity):
    """The number of open, not omitted zones of a partition.

    The arming sensor is on (blocked) while this is above 0, or while a
    fault prevents arming.
    """

    _attr_translation_key = "open_zones"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SecvestCoordinator, partition: Partition) -> None:
        """Name the sensor after the partition."""
        super().__init__(coordinator, f"partition_{partition.number}_open_zones")
        self.number = partition.number
        self._attr_translation_placeholders = {"partition": partition.name}

    @property
    def available(self) -> bool:
        """Unavailable while the panel doesn't report the partition."""
        return super().available and self.number in self.coordinator.data.partitions

    @property
    def native_value(self) -> int:
        """Return the number of open, not omitted zones."""
        return len(self.coordinator.data.open_zones(self.number))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the open zones by id and name."""
        zones = self.coordinator.data.open_zones(self.number)
        return {
            "zones": [zone.id for zone in zones],
            "summary": "\n".join(zone.name for zone in zones),
        }


def significant(value: float | None, digits: int = 2) -> float | None:
    """Round to two significant digits: 0.013, 0.42, 1.7, 6.2, 15 (#176)."""
    if value is None or value == 0:
        return value
    return round(value, digits - 1 - math.floor(math.log10(abs(value))))


@dataclass(frozen=True, kw_only=True)
class DiagnosticDescription(SensorEntityDescription):
    """A value of polling or the connection, from what rounds already know."""

    value: Callable[[SecvestCoordinator], float | int | None]
    attributes: Callable[[SecvestCoordinator], dict[str, Any]] | None = None


DIAGNOSTICS = (
    DiagnosticDescription(
        key="round_duration",
        translation_key="round_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: significant(c.round_duration),
    ),
    DiagnosticDescription(
        key="connection_setup",
        translation_key="connection_setup",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: significant(c.client.transport.stats.last_connect_time),
    ),
    DiagnosticDescription(
        key="full_handshakes",
        translation_key="full_handshakes",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda c: c.client.transport.stats.full_handshakes,
    ),
    DiagnosticDescription(
        key="failed_rounds",
        translation_key="failed_rounds",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda c: c.backoff.failures,
        attributes=lambda c: {
            "paused": c.backoff.paused,
            "last_error": c.backoff.last_error,
        },
    ),
)


class DiagnosticSensor(SecvestEntity, SensorEntity):
    """How polling and the connection are doing (#176).

    Available while the panel isn't reachable, since that is what it shows;
    mean, minimum and maximum come from Home Assistant's statistics.
    """

    entity_description: DiagnosticDescription

    def __init__(
        self, coordinator: SecvestCoordinator, description: DiagnosticDescription
    ) -> None:
        """Attach the sensor to the panel device."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Always: the values describe the connection, also a failing one."""
        return True

    @property
    def native_value(self) -> float | int | None:
        """Return the value."""
        return self.entity_description.value(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the attributes, if the sensor has any."""
        attributes = self.entity_description.attributes
        return attributes(self.coordinator) if attributes else None
