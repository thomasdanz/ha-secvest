"""Base entity classes."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .api.models import Zone
from .const import (
    CONF_EXCLUDED_ZONES,
    CONF_INSTALLATION_NAME,
    DOMAIN,
    MANUFACTURER,
    PANEL_MODEL,
)
from .coordinator import SecvestCoordinator
from .groups import ZoneGroup
from .web import WebCoordinator
from .webui.models import WebZoneStatus


def panel_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the panel device of a config entry.

    The API reports no serial number or model, so the entry identifies the
    panel. Its name is the installation's, as read at setup (the entry's
    title) or later on request in the options (#137).
    """
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=PANEL_MODEL,
        name=entry.data.get(CONF_INSTALLATION_NAME) or entry.title,
    )


class SecvestEntity(CoordinatorEntity[SecvestCoordinator]):
    """An entity on the panel device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SecvestCoordinator, key: str) -> None:
        """Attach the entity to the panel device of its config entry."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = panel_device_info(entry)

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
        "zone_group": "Zonengruppe",
    },
    "en": {
        "ip_zone": "IP zone",
        "wireless_zone": "Wireless zone",
        "wired_zone": "Wired zone",
        "zone_group": "Zone group",
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


def zone_entity_id(
    coordinator: SecvestCoordinator, zone: Zone, domain: Platform, suffix: str
) -> str:
    """Suggest <domain>.<installation>_<zone>[_<suffix>]."""
    title = coordinator.config_entry.title
    parts = (slugify(title), slugify(zone.name), suffix)
    object_id = "_".join(part for part in parts if part)
    return f"{domain}.{object_id}"


def zone_device_info(coordinator: SecvestCoordinator, zone: Zone) -> DeviceInfo:
    """Return the zone's device, linked to the panel.

    The name tells the kind of zone, e.g. "Funkzone Keller".
    """
    kind = zone_kind(zone.id)
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_zone_{zone.id}")},
        manufacturer=MANUFACTURER,
        model=zone_model(kind, coordinator.hass.config.language),
        translation_key=kind,
        translation_placeholders={"name": zone.name},
        via_device_id=coordinator.panel_device_id,
    )


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
        self.entity_id = zone_entity_id(coordinator, zone, self.platform_domain, suffix)
        self._attr_device_info = zone_device_info(coordinator, zone)

    @property
    def zone(self) -> Zone | None:
        """Return the zone from the latest round."""
        return self.coordinator.data.zones.get(self.zone_id)

    @property
    def available(self) -> bool:
        """Unavailable while the zone isn't reported."""
        return super().available and self.zone is not None


def group_entity_id(
    coordinator: SecvestCoordinator, group: ZoneGroup, domain: Platform, suffix: str
) -> str:
    """Suggest <domain>.<installation>_<group>[_<suffix>]."""
    parts = (slugify(coordinator.config_entry.title), slugify(group.name), suffix)
    return f"{domain}." + "_".join(part for part in parts if part)


def group_device_info(coordinator: SecvestCoordinator, group: ZoneGroup) -> DeviceInfo:
    """Return the group's device, linked to the panel."""
    entry = coordinator.config_entry
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_group_{group.subentry_id}")},
        # its detectors are ABUS's; an empty manufacturer shows as unknown
        manufacturer=MANUFACTURER,
        model=zone_model("zone_group", coordinator.hass.config.language),
        translation_key="zone_group",
        translation_placeholders={"name": group.name},
        via_device_id=coordinator.panel_device_id,
    )
    # used only when the device is created; afterwards the device page (or
    # reconfiguring the group) decides
    if group.area_id and (
        area := ar.async_get(coordinator.hass).async_get_area(group.area_id)
    ):
        info["suggested_area"] = area.name
    return info


class SecvestGroupEntity(SecvestEntity):
    """An entity on the device of a zone group.

    The group is a Home Assistant concept: its own device (manufacturer ABUS,
    like its detectors), and the member zones keep their devices.
    """

    # the platform of the subclass, for the suggested entity id
    platform_domain: Platform

    def __init__(
        self,
        coordinator: SecvestCoordinator,
        group: ZoneGroup,
        key: str,
        *,
        suffix: str = "",
    ) -> None:
        """Suggest <installation>_<group>[_<suffix>] as the entity id."""
        super().__init__(coordinator, f"group_{group.subentry_id}_{key}")
        self.zone_group = group
        self.entity_id = group_entity_id(
            coordinator, group, self.platform_domain, suffix
        )
        self._attr_device_info = group_device_info(coordinator, group)

    def listed_members(self) -> list[str]:
        """Return the members the selected partitions still list."""
        listed = self.coordinator.listed_zone_ids()
        return [z for z in self.zone_group.zone_ids if z in listed]


class SecvestWebZoneEntity(CoordinatorEntity[WebCoordinator]):
    """An entity on a zone's device with a value from the web interface (#56).

    Never mixed with the REST API's values: its own coordinator, read every
    few hours.
    """

    _attr_has_entity_name = True
    # the platform of the subclass, for the suggested entity id
    platform_domain: Platform

    def __init__(
        self, web: WebCoordinator, panel: SecvestCoordinator, zone: Zone, key: str
    ) -> None:
        """Attach the entity to the zone's device."""
        super().__init__(web)
        self.zone_id = zone.id
        entry = panel.config_entry
        self._attr_unique_id = f"{entry.entry_id}_zone_{zone.id}_{key}"
        self.entity_id = zone_entity_id(panel, zone, self.platform_domain, key)
        self._attr_device_info = zone_device_info(panel, zone)

    @property
    def available(self) -> bool:
        """Unavailable only while a failed login or the password blocks reads.

        A failed read keeps the last values, so a read every few hours
        doesn't leave the entities unavailable for hours.
        """
        return not self.coordinator.blocked

    @property
    def status(self) -> WebZoneStatus | None:
        """Return the zone's row of the last read, if any."""
        state = self.coordinator.data
        return None if state is None else state.zones.get(self.zone_id)

    def _read_attributes(self) -> dict[str, Any]:
        state = self.coordinator.data
        return {"last_read": None if state is None else state.read_at.isoformat()}


def web_zones(entry: ConfigEntry) -> list[Zone]:
    """Return the wireless zones that get web interface entities.

    The zones of the selected partitions, without excluded ones; only
    wireless zones are in the web interface's wireless zone list.
    """
    coordinator: SecvestCoordinator = entry.runtime_data
    excluded = set(entry.options.get(CONF_EXCLUDED_ZONES, []))
    return [
        zone
        for zone in coordinator.data.zones.values()
        if zone.id not in excluded and zone_kind(zone.id) == "wireless_zone"
    ]


class SecvestWebGroupEntity(CoordinatorEntity[WebCoordinator]):
    """An entity on a zone group's device from its members' web values (#200).

    Like the members' web entities (#56): its own coordinator, never mixed
    with the REST API's values.
    """

    _attr_has_entity_name = True
    # the platform of the subclass, for the suggested entity id
    platform_domain: Platform

    def __init__(
        self,
        web: WebCoordinator,
        panel: SecvestCoordinator,
        group: ZoneGroup,
        key: str,
    ) -> None:
        """Attach the entity to the group's device."""
        super().__init__(web)
        self.zone_group = group
        self.panel = panel
        entry = panel.config_entry
        self._attr_unique_id = f"{entry.entry_id}_group_{group.subentry_id}_{key}"
        self.entity_id = group_entity_id(panel, group, self.platform_domain, key)
        self._attr_device_info = group_device_info(panel, group)

    @property
    def available(self) -> bool:
        """As the members' web entities: unavailable only while blocked."""
        return not self.coordinator.blocked

    def member_statuses(self) -> list[WebZoneStatus | None]:
        """Return the listed members' rows of the last read (None if missing)."""
        state = self.coordinator.data
        listed = self.panel.listed_zone_ids()
        return [
            None if state is None else state.zones.get(zone_id)
            for zone_id in self.zone_group.zone_ids
            if zone_id in listed
        ]

    def _read_attributes(self) -> dict[str, Any]:
        state = self.coordinator.data
        return {"last_read": None if state is None else state.read_at.isoformat()}
