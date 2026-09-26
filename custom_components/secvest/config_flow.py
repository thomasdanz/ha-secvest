"""Config flow for the ABUS Secvest integration."""

from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.loader import async_get_integration
import voluptuous as vol

from .api.client import Client
from .api.errors import (
    AuthenticationError,
    CommunicationError,
    InstallerLockedError,
    SecvestError,
)
from .api.models import Partition
from .api.transport import Transport
from .const import (
    CONF_ADVANCED,
    CONF_PARTITIONS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    DEFAULT_PORT,
    DOMAIN,
    TESTED_FIRMWARE,
    TESTED_MODEL,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(),
        vol.Required(CONF_USER_CODE): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL, default=False): bool,
        vol.Required(CONF_ADVANCED): section(
            vol.Schema({vol.Optional(CONF_USER_AGENT, default=""): TextSelector()}),
            {"collapsed": True},
        ),
    }
)


def normalize_address(value: str) -> str:
    """Return the panel's address as https://host:port[/path].

    Without a scheme the panel's own port applies unless one is given; an
    https URL without a port means 443 (e.g. a reverse proxy).
    """
    value = value.strip()
    has_scheme = "://" in value
    parts = urlsplit(value if has_scheme else f"https://{value}")
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("not an https address")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("unexpected parts in the address")
    port = parts.port or (443 if has_scheme else DEFAULT_PORT)
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{port}{parts.path.rstrip('/')}"


async def default_user_agent(hass: Any) -> str:
    """Return ha-secvest/<version> from the manifest."""
    integration = await async_get_integration(hass, DOMAIN)
    return f"ha-secvest/{integration.version}"


class SecvestConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a panel."""

    VERSION = 1

    def __init__(self) -> None:
        """Start without a checked connection."""
        self._data: dict[str, Any] = {}
        self._title = ""
        self._partitions: list[Partition] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for address and credentials and check them once."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_address(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_address"
            else:
                await self.async_set_unique_id(url.removeprefix("https://"))
                self._abort_if_unique_id_configured()
                user_agent = user_input[CONF_ADVANCED].get(CONF_USER_AGENT, "").strip()
                data = {
                    CONF_URL: url,
                    CONF_USER_CODE: user_input[CONF_USER_CODE].strip(),
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    CONF_USER_AGENT: user_agent,
                }
                error = await self._validate(data)
                if error is None:
                    self._data = data
                    return await self.async_step_partitions()
                errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
            description_placeholders={
                "model": TESTED_MODEL,
                "firmware": TESTED_FIRMWARE,
            },
        )

    async def async_step_partitions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user select the partitions; the zones follow from them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = sorted(int(value) for value in user_input[CONF_PARTITIONS])
            if selected:
                return self.async_create_entry(
                    title=self._title,
                    data=self._data,
                    options={CONF_PARTITIONS: selected},
                )
            errors["base"] = "no_partitions"
        # the panel doesn't reveal the user's rights, so all partitions are
        # offered; those without zones are deselected by default
        default = [str(p.number) for p in self._partitions if p.zone_ids]
        schema = vol.Schema(
            {
                vol.Required(CONF_PARTITIONS, default=default): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=str(p.number), label=f"{p.number}: {p.name}"
                            )
                            for p in self._partitions
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="partitions", data_schema=schema, errors=errors
        )

    async def _validate(self, data: dict[str, Any]) -> str | None:
        """Check the credentials, then read the partitions; never retried.

        The partitions are only read once the credentials were accepted.
        """
        transport = Transport(
            data[CONF_URL],
            data[CONF_USER_CODE],
            data[CONF_PASSWORD],
            verify_ssl=data[CONF_VERIFY_SSL],
            user_agent=data[CONF_USER_AGENT] or await default_user_agent(self.hass),
        )
        try:
            client = Client(transport)
            system = await client.get_system()
            self._partitions = await client.get_partitions()
        except AuthenticationError:
            return "invalid_auth"
        except InstallerLockedError:
            return "installer_locked"
        except CommunicationError as err:
            if isinstance(err.__cause__, TimeoutError):
                return "timeout"
            return "cannot_connect"
        except SecvestError:
            return "unexpected_response"
        finally:
            await transport.close()
        self._title = system.name
        return None
