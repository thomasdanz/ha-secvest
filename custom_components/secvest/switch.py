"""Switches: omitting a zone for one arming cycle."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import Zone
from .commands import async_set_group_omitted, async_set_omitted
from .const import CONF_EXCLUDED_ZONES
from .coordinator import SecvestCoordinator
from .entity import SecvestGroupEntity, SecvestZoneEntity
from .groups import ZoneGroup, zone_groups

# commands wait for the request queue themselves
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add an omit switch per omittable zone and per zone group with one."""
    coordinator = entry.runtime_data
    excluded = frozenset(entry.options.get(CONF_EXCLUDED_ZONES, []))
    zones = coordinator.data.zones
    async_add_entities(
        OmitSwitch(coordinator, zone)
        for zone in zones.values()
        if zone.omittable and zone.id not in excluded
    )
    # each group's switch belongs to its subentry, like the group sensor
    for group in zone_groups(entry):
        if any(
            (zone := zones.get(zone_id)) is not None
            and zone.omittable
            and zone_id not in excluded
            for zone_id in group.zone_ids
        ):
            async_add_entities(
                [GroupOmitSwitch(coordinator, group, excluded)],
                config_subentry_id=group.subentry_id,
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


class GroupOmitSwitch(SecvestGroupEntity, SwitchEntity):
    """On while all omittable members of a zone group are omitted (#136).

    Turning it on omits the omittable members not omitted yet, one after
    the other; turning it off includes them again. Members that can't be
    omitted are never sent and are listed as an attribute, so a partly
    guarded opening shows.
    """

    _attr_translation_key = "omit"
    platform_domain = Platform.SWITCH

    def __init__(
        self,
        coordinator: SecvestCoordinator,
        group: ZoneGroup,
        excluded: frozenset[str],
    ) -> None:
        """Suggest <installation>_<group>_omit as the entity id."""
        super().__init__(coordinator, group, "omit", suffix="omit")
        self._excluded = excluded

    def _members(self) -> list[Zone]:
        """Return the listed members that were read."""
        zones = self.coordinator.data.zones
        return [zones[z] for z in self.listed_members() if z in zones]

    def _omittable(self) -> list[Zone]:
        return [
            zone
            for zone in self._members()
            if zone.omittable and zone.id not in self._excluded
        ]

    @property
    def available(self) -> bool:
        """Unavailable while no omittable member is read."""
        return super().available and bool(self._omittable())

    @property
    def is_on(self) -> bool:
        """On while every omittable member is omitted."""
        return all(zone.omitted for zone in self._omittable())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the omitted members and those that can't be omitted."""
        return {
            "omitted_zones": [z.id for z in self._members() if z.omitted],
            "not_omittable_zones": [
                z.id
                for z in self._members()
                if not z.omittable or z.id in self._excluded
            ],
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Omit the omittable members not omitted yet, verified."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Include the omitted members again, verified."""
        await self._set(False)

    async def _set(self, omitted: bool) -> None:
        await async_set_group_omitted(
            self.coordinator,
            self.zone_group.name,
            [zone.id for zone in self._omittable()],
            omitted,
        )
