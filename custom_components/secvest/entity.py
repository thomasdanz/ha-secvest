"""Base entity classes."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api.models import Zone
from .const import DOMAIN, MANUFACTURER
from .coordinator import SecvestCoordinator


class SecvestEntity(CoordinatorEntity[SecvestCoordinator]):
    """An entity on the panel device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SecvestCoordinator, key: str) -> None:
        """Attach the entity to the panel device of its config entry."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # the API reports no serial number or model, so the entry identifies
        # the panel
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer=MANUFACTURER,
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        """Stay available on single failed rounds; see the coordinator."""
        return self.coordinator.available


class SecvestZoneEntity(SecvestEntity):
    """An entity on the device of one zone (detector)."""

    def __init__(self, coordinator: SecvestCoordinator, zone: Zone, key: str) -> None:
        """Attach the entity to the zone's device, linked to the panel."""
        super().__init__(coordinator, f"zone_{zone.id}_{key}")
        self.zone_id = zone.id
        entry_id = coordinator.config_entry.entry_id
        # only the device changes when zones are grouped later (#67); the
        # unique id stays
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_zone_{zone.id}")},
            manufacturer=MANUFACTURER,
            name=zone.name,
            via_device_id=coordinator.panel_device_id,
        )

    @property
    def zone(self) -> Zone | None:
        """Return the zone from the latest round."""
        return self.coordinator.data.zones.get(self.zone_id)

    @property
    def available(self) -> bool:
        """Unavailable while the zone isn't reported."""
        return super().available and self.zone is not None
