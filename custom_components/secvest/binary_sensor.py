"""Binary sensors."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import FaultType, PanelEvent, Partition, Zone, ZoneState
from .const import CONF_ZONE_DEVICE_CLASSES
from .coordinator import SecvestCoordinator
from .entity import SecvestEntity, SecvestZoneEntity

# the entities only read the coordinator's state
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors of a panel and its zones."""
    coordinator = entry.runtime_data
    device_classes = entry.options.get(CONF_ZONE_DEVICE_CLASSES, {})
    entities: list[BinarySensorEntity] = [
        InstallerLockSensor(coordinator, "installer_lock"),
        ProblemSensor(coordinator, "problem"),
    ]
    partitions = coordinator.data.partitions
    entities.extend(
        ArmingBlockedSensor(coordinator, partitions[number])
        for number in coordinator.selected_partitions
        if number in partitions
    )
    for zone in coordinator.data.zones.values():
        entities.append(ZoneSensor(coordinator, zone, device_classes.get(zone.id)))
        entities.append(ZoneProblemSensor(coordinator, zone, "problem"))
    async_add_entities(entities)


class InstallerLockSensor(SecvestEntity, BinarySensorEntity):
    """On while the installer is logged in, which locks the panel's API."""

    _attr_translation_key = "installer_lock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        """Return whether the panel is locked."""
        return self.coordinator.installer_locked


class ProblemSensor(SecvestEntity, BinarySensorEntity):
    """On while any fault other than an open zone is present."""

    _attr_translation_key = "problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Return whether the panel reports a fault other than an open zone."""
        # "zone open" appears for every open omittable zone, even when
        # disarmed; the zone sensors and "arming blocked" cover it
        return any(
            fault.type != FaultType.ZONE_OPEN for fault in self.coordinator.data.faults
        )


class ArmingBlockedSensor(SecvestEntity, BinarySensorEntity):
    """On while a fault that prevents arming affects the partition.

    The panel evaluates blocking conditions for the requested state, so
    arming can still fail while this is off (e.g. an open entry door on the
    reference panel is no fault).
    """

    _attr_translation_key = "arming_blocked"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SecvestCoordinator, partition: Partition) -> None:
        """Name the sensor after the partition."""
        super().__init__(coordinator, f"partition_{partition.number}_arming_blocked")
        self.number = partition.number
        self._attr_translation_placeholders = {"partition": partition.name}

    @property
    def available(self) -> bool:
        """Unavailable while the panel doesn't report the partition."""
        return super().available and self.number in self.coordinator.data.partitions

    @property
    def is_on(self) -> bool:
        """Return whether a blocking fault affects the partition."""
        return bool(self._blocking())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Name the blocking faults and zones."""
        blocking = self._blocking()
        return {
            "blocking_zones": [f.zone_id for f in blocking if f.zone_id],
            "blocking_faults": [f.text or f"{f.type}/{f.id}" for f in blocking],
        }

    def _blocking(self) -> list[PanelEvent]:
        return [
            fault
            for fault in self.coordinator.data.faults
            if fault.prevents_set and self.number in fault.partitions
        ]


class ZoneSensor(SecvestZoneEntity, BinarySensorEntity):
    """On while the zone is open; the main entity of the zone's device."""

    # named after the device, i.e. the zone
    _attr_name = None

    def __init__(
        self,
        coordinator: SecvestCoordinator,
        zone: Zone,
        device_class: str | None,
    ) -> None:
        """Use the device class from the options; the API has no detector type."""
        super().__init__(coordinator, zone, "open")
        if device_class is not None and device_class in BinarySensorDeviceClass:
            self._attr_device_class = BinarySensorDeviceClass(device_class)

    @property
    def is_on(self) -> bool | None:
        """Open or closed; other states (tamper, fault) are unknown."""
        zone = self.zone
        if zone is None or zone.state not in (ZoneState.OPEN, ZoneState.CLOSED):
            return None
        return zone.state == ZoneState.OPEN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the zone as the panel reports it."""
        zone = self.zone
        if zone is None:
            return {}
        partitions = self.coordinator.data.partitions.values()
        return {
            "zone_id": zone.id,
            "zone_state": str(zone.state),
            # all partitions the zone belongs to, not only the selected ones
            "partitions": [p.number for p in partitions if zone.id in p.zone_ids],
            "omittable": zone.omittable,
            "omitted": zone.omitted,
            "inner": zone.inner,
        }


class ZoneProblemSensor(SecvestZoneEntity, BinarySensorEntity):
    """On for a zone state other than open/closed or a fault on the zone."""

    _attr_translation_key = "zone_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return whether the zone has a problem."""
        zone = self.zone
        if zone is None:
            return None
        if zone.state not in (ZoneState.OPEN, ZoneState.CLOSED):
            return True
        # "zone open" appears for every open omittable zone, even when
        # disarmed; the zone sensor already shows it
        return any(
            fault.zone_id == zone.id and fault.type != FaultType.ZONE_OPEN
            for fault in self.coordinator.data.faults
        )
