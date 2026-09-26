"""The ABUS Secvest integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr

from .api.client import Client
from .api.transport import Transport
from .config_flow import default_user_agent
from .const import (
    CONF_AUTH_FAILED,
    CONF_EXCLUDED_ZONES,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import SecvestCoordinator, clear_partition_issues

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

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
        name=entry.title,
    )
    coordinator.panel_device_id = panel.id
    entry.runtime_data = coordinator
    _remove_orphaned_devices(hass, entry, coordinator)
    coordinator.clear_partition_issues()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _remove_orphaned_devices(
    hass: HomeAssistant, entry: SecvestConfigEntry, coordinator: SecvestCoordinator
) -> None:
    """Remove the devices of zones that are no longer selected or excluded.

    Decided from the partitions' zone lists, not from the zones read, so a
    zone the panel briefly doesn't report keeps its device and settings.
    """
    partitions = coordinator.data.partitions
    excluded = set(entry.options.get(CONF_EXCLUDED_ZONES, []))
    wanted = {(DOMAIN, entry.entry_id)} | {
        (DOMAIN, f"{entry.entry_id}_zone_{zone_id}")
        for number in coordinator.selected_partitions
        if number in partitions
        for zone_id in partitions[number].zone_ids
        if zone_id not in excluded
    }
    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if not device.identifiers & wanted:
            registry.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )


async def async_remove_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> None:
    """Remove the repair issues of a deleted entry."""
    clear_partition_issues(hass, entry.entry_id, keep=())


async def async_unload_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Remove the entities, then close the connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.transport.close()
    return unloaded
