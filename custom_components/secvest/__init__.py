"""The ABUS Secvest integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant

from .api.client import Client
from .api.transport import Transport
from .config_flow import default_user_agent
from .const import CONF_USER_AGENT, CONF_USER_CODE
from .coordinator import SecvestCoordinator

PLATFORMS = [Platform.BINARY_SENSOR]

type SecvestConfigEntry = ConfigEntry[SecvestCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Connect to the panel and run the first polling round."""
    data = entry.data
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
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Remove the entities, then close the connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.transport.close()
    return unloaded
