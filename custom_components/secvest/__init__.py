"""The ABUS Secvest integration."""

import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .api.client import Client
from .api.transport import Transport
from .config_flow import default_user_agent
from .const import (
    CONF_AUTH_FAILED,
    CONF_EXCLUDED_ZONES,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    CONF_ZONE_DEVICE_CLASSES,
    DOMAIN,
    MANUFACTURER,
    PANEL_MODEL,
)
from .coordinator import SecvestCoordinator, clear_issues
from .groups import reload_snapshot, zone_groups

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

type SecvestConfigEntry = ConfigEntry[SecvestCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Connect to the panel and run the first polling round."""
    data = entry.data
    if data.get(CONF_AUTH_FAILED):
        # the panel rejected these credentials before, maybe before a
        # restart; never send them again (#6)
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        )
    transport = Transport(
        data[CONF_URL],
        data[CONF_USER_CODE],
        data[CONF_PASSWORD],
        verify_ssl=data[CONF_VERIFY_SSL],
        user_agent=data[CONF_USER_AGENT] or await default_user_agent(hass),
    )
    coordinator = SecvestCoordinator(hass, entry, Client(transport))
    # after a reload shortly after a round, its result is taken instead of
    # waiting for the minimum spacing
    if not coordinator.reuse_recent_round():
        try:
            await coordinator.async_config_entry_first_refresh()
        except BaseException:
            # a failed setup is not unloaded; stop the transport's thread here
            await transport.close()
            raise
    # registered first, so that the zone devices can refer to it
    panel = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=PANEL_MODEL,
        name=entry.title,
    )
    coordinator.panel_device_id = panel.id
    entry.runtime_data = coordinator
    _remove_orphaned_devices(hass, entry, coordinator)
    coordinator.clear_stale_issues()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _remove_stale_entities(hass, entry)
    _hide_grouped_zones(hass, entry)
    # changed options or zone groups (subentries) reload the entry
    snapshot = reload_snapshot(entry)

    async def _reload_on_change(hass: HomeAssistant, entry: SecvestConfigEntry) -> None:
        if reload_snapshot(entry) != snapshot:
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_on_change))
    return True


_ZONE_ENTITY = re.compile(r"zone_([^_]+)_(open|problem)")


def _hide_grouped_zones(hass: HomeAssistant, entry: SecvestConfigEntry) -> None:
    """Hide the entities of zones in a group that asks for it, show the rest.

    Only entities hidden by the integration are shown again; those the user
    hid stay hidden.
    """
    hidden = {
        zone_id
        for group in zone_groups(entry)
        if group.hide_members
        for zone_id in group.zone_ids
    }
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        match = _ZONE_ENTITY.fullmatch(entity.unique_id.removeprefix(prefix))
        if match is None:
            continue
        if match[1] in hidden and entity.hidden_by is None:
            registry.async_update_entity(
                entity.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
            )
        elif (
            match[1] not in hidden
            and entity.hidden_by is er.RegistryEntryHider.INTEGRATION
        ):
            registry.async_update_entity(entity.entity_id, hidden_by=None)


async def async_migrate_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Bring an entry stored by an older version up to date.

    Home Assistant refuses entries of a newer major version itself; a newer
    minor version is compatible by definition and loads as it is.
    """
    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        # 1.2: setup stores the zone settings too (0.1.7); older entries get
        # the defaults the code assumed so far
        options = {
            CONF_EXCLUDED_ZONES: [],
            CONF_ZONE_DEVICE_CLASSES: {},
            **entry.options,
        }
        hass.config_entries.async_update_entry(entry, options=options, minor_version=2)
    return True


# the kinds of entities this version provides, by unique id after the entry
# id; registry entries of other kinds come from an older version
_ENTITY_KINDS = re.compile(
    r"(installer_lock|problem|faults"
    r"|partition_\d+_(alarm|arming_blocked|open_zones|acknowledge)"
    r"|zone_[^_]+_(open|problem)"
    r"|group_[^_]+_open)"
)


def _remove_stale_entities(hass: HomeAssistant, entry: SecvestConfigEntry) -> None:
    """Remove entities of kinds that no longer exist.

    Zones and partitions that are only temporarily missing keep theirs; the
    kind is what counts.
    """
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        kind = entity.unique_id.removeprefix(prefix)
        if not _ENTITY_KINDS.fullmatch(kind):
            _LOGGER.info(
                "Removing %s, which this version no longer provides", entity.entity_id
            )
            registry.async_remove(entity.entity_id)


def _remove_orphaned_devices(
    hass: HomeAssistant, entry: SecvestConfigEntry, coordinator: SecvestCoordinator
) -> None:
    """Remove the devices of zones that are no longer selected or excluded.

    Decided from the partitions' zone lists, not from the zones read, so a
    zone the panel briefly doesn't report keeps its device and settings.
    """
    partitions = coordinator.data.partitions
    excluded = set(entry.options.get(CONF_EXCLUDED_ZONES, []))
    wanted = (
        {(DOMAIN, entry.entry_id)}
        | {
            (DOMAIN, f"{entry.entry_id}_group_{group.subentry_id}")
            for group in zone_groups(entry)
        }
        | {
            (DOMAIN, f"{entry.entry_id}_zone_{zone_id}")
            for number in coordinator.selected_partitions
            if number in partitions
            for zone_id in partitions[number].zone_ids
            if zone_id not in excluded
        }
    )
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> None:
    """Remove the repair issues of a deleted entry."""
    clear_issues(hass, entry.entry_id, keep=())


async def async_unload_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Remove the entities, then close the connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.transport.close()
    return unloaded
