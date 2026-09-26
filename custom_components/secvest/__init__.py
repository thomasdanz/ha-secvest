"""The ABUS Secvest integration."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration; config entries follow with the config flow."""
    return True
