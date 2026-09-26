"""The ABUS Secvest integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

from .api.client import Client
from .api.transport import Transport
from .config_flow import default_user_agent
from .const import CONF_USER_AGENT, CONF_USER_CODE

type SecvestConfigEntry = ConfigEntry[Client]


async def async_setup_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Create the client for a panel; nothing is sent yet."""
    data = entry.data
    transport = Transport(
        data[CONF_URL],
        data[CONF_USER_CODE],
        data[CONF_PASSWORD],
        verify_ssl=data[CONF_VERIFY_SSL],
        user_agent=data[CONF_USER_AGENT] or await default_user_agent(hass),
    )
    entry.runtime_data = Client(transport)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SecvestConfigEntry) -> bool:
    """Close the connection and stop the transport's thread."""
    await entry.runtime_data.transport.close()
    return True
