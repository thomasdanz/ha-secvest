"""Switches: omitting a zone for one arming cycle."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import Zone
from .commands import async_set_omitted
from .const import CONF_EXCLUDED_ZONES
from .coordinator import SecvestCoordinator
from .entity import SecvestZoneEntity

# commands wait for the request queue themselves
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an omit switch per omittable zone."""
    coordinator = entry.runtime_data
    excluded = set(entry.options.get(CONF_EXCLUDED_ZONES, []))
    async_add_entities(
        OmitSwitch(coordinator, zone)
        for zone in coordinator.data.zones.values()
        if zone.omittable and zone.id not in excluded
    )


class OmitSwitch(SecvestZoneEntity, SwitchEntity):
    """On while the zone is omitted.

    The panel includes omitted zones again at the next disarm; the switch
    follows that with the next round.
    """

    _attr_translation_key = "omit"
    platform_domain = Platform.SWITCH

    def __init__(self, coordinator: SecvestCoordinator, zone: Zone) -> None:
        """Suggest <installation>_<zone>_omit as the entity id."""
        super().__init__(coordinator, zone, "omit", suffix="omit")

    @property
    def is_on(self) -> bool | None:
        """Return whether the zone is omitted."""
        zone = self.zone
        return None if zone is None else zone.omitted

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Omit the zone, verified."""
        await async_set_omitted(self.coordinator, self.zone_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Include the zone again, verified."""
        await async_set_omitted(self.coordinator, self.zone_id, False)
