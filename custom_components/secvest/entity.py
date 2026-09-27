"""Base entity classes."""

from homeassistant.const import Platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .api.models import Zone
from .const import DOMAIN, MANUFACTURER, PANEL_MODEL
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
            model=PANEL_MODEL,
            name=entry.title,
        )

    @property
    def available(self) -> bool:
        """Stay available on single failed rounds; see the coordinator."""
        return self.coordinator.available


# zone numbering from the user manual and the installer web interface (see
# the specification); 4-wire and HyMo zones are numbered differently or
# unknown, so they keep the plain name
_ZONE_KINDS = (
    (range(101, 107), "ip_zone"),
    (range(201, 249), "wireless_zone"),
    (range(301, 305), "wired_zone"),
)


# the device registry's model can't be translated like the name, so it is
# set in Home Assistant's language (updated at every start)
_ZONE_MODELS = {
    "de": {
        "ip_zone": "IP-Zone",
        "wireless_zone": "Funkzone",
        "wired_zone": "Drahtzone",
    },
    "en": {
        "ip_zone": "IP zone",
        "wireless_zone": "Wireless zone",
        "wired_zone": "Wired zone",
    },
}


def zone_model(kind: str, language: str) -> str | None:
    """Return the model for a kind of zone, in German or else English."""
    models = _ZONE_MODELS.get(language.split("-", maxsplit=1)[0], _ZONE_MODELS["en"])
    return models.get(kind)


def zone_kind(zone_id: str) -> str:
    """Return the translation key for the kind of zone, from its number."""
    if zone_id.isdigit():
        for numbers, kind in _ZONE_KINDS:
            if int(zone_id) in numbers:
                return kind
    return "zone"


class SecvestZoneEntity(SecvestEntity):
    """An entity on the device of one zone (detector)."""

    # the platform of the subclass, for the suggested entity id
    platform_domain: Platform

    def __init__(
        self,
        coordinator: SecvestCoordinator,
        zone: Zone,
        key: str,
        *,
        suffix: str = "",
    ) -> None:
        """Attach the entity to the zone's device, linked to the panel.

        The device is named after the zone, so Home Assistant would derive
        the entity id from the zone name alone; the suggested id adds the
        installation's name, like the panel's entities have it:
        <domain>.<installation>_<zone>[_<suffix>]. Only used when the entity
        is registered; users can rename it.
        """
        super().__init__(coordinator, f"zone_{zone.id}_{key}")
        self.zone_id = zone.id
        entry = coordinator.config_entry
        object_id = "_".join(
            part for part in (slugify(entry.title), slugify(zone.name), suffix) if part
        )
        self.entity_id = f"{self.platform_domain}.{object_id}"
        entry_id = entry.entry_id
        # only the device changes when zones are grouped later (#67); the
        # unique id stays
        # the name tells the kind of zone, e.g. "Funkzone Keller"
        kind = zone_kind(zone.id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_zone_{zone.id}")},
            manufacturer=MANUFACTURER,
            model=zone_model(kind, coordinator.hass.config.language),
            translation_key=kind,
            translation_placeholders={"name": zone.name},
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
