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
from .api.models import Zone, ZoneState
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
        InstallerLockSensor(coordinator, "installer_lock")
    ]
    entities.extend(
        ZoneSensor(coordinator, zone, device_classes.get(zone.id))
        for zone in coordinator.data.zones.values()
    )
    async_add_entities(entities)


class InstallerLockSensor(SecvestEntity, BinarySensorEntity):
    """On while the installer is logged in, which locks the panel's API."""

    _attr_translation_key = "installer_lock"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        """Return whether the panel is locked."""
        return self.coordinator.installer_locked


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
