"""Base entity classes."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
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
            manufacturer="ABUS",
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        """Stay available on single failed rounds; see the coordinator."""
        return self.coordinator.available
