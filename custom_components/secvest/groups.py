"""Zone groups: several zones of one opening, configured as subentries.

A zone group is a Home Assistant concept, not one of the panel: it gets its
own device and sensor, and the member zones keep theirs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_URL,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_AREA_ID,
    CONF_CODES,
    CONF_HIDE_MEMBERS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    CONF_ZONES,
    DOMAIN,
    SUBENTRY_ZONE_GROUP,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

# a group's device class that follows its zones
SAME_AS_ZONES = "same_as_zones"


@dataclass(frozen=True, slots=True)
class ZoneGroup:
    """One zone group as stored in its subentry."""

    subentry_id: str
    name: str
    zone_ids: tuple[str, ...]
    device_class: str
    hide_members: bool
    # for a new group device only
    area_id: str | None = None


def zone_groups(entry: ConfigEntry) -> list[ZoneGroup]:
    """Return the zone groups of an entry."""
    return [
        ZoneGroup(
            subentry_id=subentry.subentry_id,
            name=subentry.data[CONF_NAME],
            zone_ids=tuple(subentry.data[CONF_ZONES]),
            device_class=subentry.data[CONF_DEVICE_CLASS],
            hide_members=subentry.data.get(CONF_HIDE_MEMBERS, False),
            area_id=subentry.data.get(CONF_AREA_ID),
        )
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_ZONE_GROUP
    ]


def reload_snapshot(entry: ConfigEntry) -> object:
    """Return the settings a change of which reloads the entry.

    Options, zone groups, the User-Agent, and the address, credentials and
    certificate check, which the reconfiguration changes (#138). The flag a
    401 sets is left out; clearing it reloads by itself. Codes are left
    out too: the alarm panels read them at each command (#141).
    """
    return (
        {key: value for key, value in entry.options.items() if key != CONF_CODES},
        {
            subentry_id: (subentry.title, dict(subentry.data))
            for subentry_id, subentry in entry.subentries.items()
        },
        entry.data.get(CONF_USER_AGENT),
        tuple(
            entry.data.get(key)
            for key in (CONF_URL, CONF_USER_CODE, CONF_PASSWORD, CONF_VERIFY_SSL)
        ),
    )


def zones_device_class(
    hass: HomeAssistant, entry: ConfigEntry, zone_ids: tuple[str, ...] | list[str]
) -> str | None:
    """Return the device class all zones show, or None if they differ.

    What the zone sensors show counts: the user's "Show as" in Home
    Assistant, else the class from the options.
    """
    registry = er.async_get(hass)
    classes = set()
    for zone_id in zone_ids:
        entity_id = registry.async_get_entity_id(
            Platform.BINARY_SENSOR, DOMAIN, f"{entry.entry_id}_zone_{zone_id}_open"
        )
        entity = registry.async_get(entity_id) if entity_id else None
        classes.add(
            entity.device_class or entity.original_device_class if entity else None
        )
    if len(classes) != 1:
        return None
    return classes.pop()
