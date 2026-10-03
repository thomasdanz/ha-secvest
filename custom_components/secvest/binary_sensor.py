"""Binary sensors."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import FaultType, PanelEvent, Partition, Zone, ZoneState
from .const import CONF_EXCLUDED_ZONES, CONF_ZONE_DEVICE_CLASSES
from .coordinator import SecvestCoordinator
from .entity import SecvestEntity, SecvestGroupEntity, SecvestZoneEntity
from .groups import SAME_AS_ZONES, ZoneGroup, zone_groups, zones_device_class

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
    excluded = set(entry.options.get(CONF_EXCLUDED_ZONES, []))
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
        if zone.id in excluded:
            continue
        entities.append(ZoneSensor(coordinator, zone, device_classes.get(zone.id)))
        entities.append(ZoneProblemSensor(coordinator, zone))
    async_add_entities(entities)
    # each group belongs to its subentry, so deleting the group removes it
    for group in zone_groups(entry):
        async_add_entities(
            [ZoneGroupSensor(coordinator, group)],
            config_subentry_id=group.subentry_id,
        )


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
        """Return whether there are faults; the faults sensor counts them."""
        return bool(self.coordinator.data.problems)


class ArmingBlockedSensor(SecvestEntity, BinarySensorEntity):
    """On while open zones or a blocking fault prevent arming the partition.

    Open zones count whether or not the panel lists them as faults: an open
    entry door is no fault on the reference panel, but arming via the API
    still fails then. Other faults count if they prevent arming.
    """

    _attr_translation_key = "arming_blocked"

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
        """Return whether open zones or a blocking fault prevent arming."""
        state = self.coordinator.data
        return bool(
            state.open_zones(self.number) or state.blocking_problems(self.number)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Name the open zones and the blocking faults."""
        state = self.coordinator.data
        return {
            "blocking_zones": [zone.id for zone in state.open_zones(self.number)],
            "blocking_faults": [
                f.text or f"{f.type}/{f.id}"
                for f in state.blocking_problems(self.number)
            ],
        }


class ZoneSensor(SecvestZoneEntity, BinarySensorEntity):
    """On while the zone is open; the main entity of the zone's device."""

    # named after the device, i.e. the zone
    _attr_name = None
    platform_domain = Platform.BINARY_SENSOR

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
    platform_domain = Platform.BINARY_SENSOR

    def __init__(self, coordinator: SecvestCoordinator, zone: Zone) -> None:
        """Suggest <installation>_<zone>_problem as the entity id."""
        super().__init__(coordinator, zone, "problem", suffix="problem")

    @property
    def is_on(self) -> bool | None:
        """Return whether the zone has a problem."""
        zone = self.zone
        if zone is None:
            return None
        if zone.state not in (ZoneState.OPEN, ZoneState.CLOSED):
            return True
        return bool(self._faults())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the zone's faults, readable without the faults sensor."""
        return {"faults": [f.text or f"{f.type}/{f.id}" for f in self._faults()]}

    def _faults(self) -> list[PanelEvent]:
        # "zone open" appears for every open omittable zone, even when
        # disarmed; the zone sensor already shows it
        return [
            fault
            for fault in self.coordinator.data.faults
            if fault.zone_id == self.zone_id and fault.type != FaultType.ZONE_OPEN
        ]


class ZoneGroupSensor(SecvestGroupEntity, BinarySensorEntity):
    """On while any zone of a group is open; the group device's main entity."""

    # named after the device, i.e. the group
    _attr_name = None
    platform_domain = Platform.BINARY_SENSOR

    def __init__(self, coordinator: SecvestCoordinator, group: ZoneGroup) -> None:
        """Take the device class from the group, or from its zones."""
        super().__init__(coordinator, group, "open")
        entry = coordinator.config_entry
        device_class: str | None = group.device_class
        if device_class == SAME_AS_ZONES:
            device_class = zones_device_class(coordinator.hass, entry, group.zone_ids)
        if device_class is not None and device_class in BinarySensorDeviceClass:
            self._attr_device_class = BinarySensorDeviceClass(device_class)

    @property
    def available(self) -> bool:
        """Unavailable while no member is listed or read."""
        zones = self.coordinator.data.zones
        return super().available and any(z in zones for z in self.listed_members())

    @property
    def is_on(self) -> bool | None:
        """On if a member is open; off if all listed members are closed.

        Members the partitions no longer list don't count (a repair issue
        says so); a listed member not read right now makes it unknown.
        """
        zones = self.coordinator.data.zones
        members = [zones.get(zone_id) for zone_id in self.listed_members()]
        if any(z is not None and z.state == ZoneState.OPEN for z in members):
            return True
        if all(z is not None and z.state == ZoneState.CLOSED for z in members):
            return False
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the member zones and the open ones."""
        zones = self.coordinator.data.zones
        listed = self.listed_members()
        return {
            "zones": list(self.zone_group.zone_ids),
            "open_zones": [
                z for z in listed if z in zones and zones[z].state == ZoneState.OPEN
            ],
            "missing_zones": [z for z in self.zone_group.zone_ids if z not in listed],
        }
