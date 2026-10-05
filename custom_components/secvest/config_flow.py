"""Config flow for the ABUS Secvest integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
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
from .api.models import Partition, Zone
from .api.transport import Transport
from .const import (
    CONF_ADVANCED,
    CONF_AUTH_FAILED,
    CONF_CODES,
    CONF_EXCLUDED_ZONES,
    CONF_PARTITIONS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    CONF_ZONE_DEVICE_CLASSES,
    DEFAULT_PORT,
    DOMAIN,
    SUBENTRY_ZONE_GROUP,
    TESTED_FIRMWARE,
    TESTED_MODEL,
)
from .flow_helpers import partition_selector, zone_labels, zone_options, zone_schema
from .options_flow import SecvestOptionsFlow
from .zone_group_flow import ZoneGroupFlow

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USER_CODE): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
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

    # stored data: VERSION changes break compatibility, MINOR_VERSION
    # changes don't; each step is migrated in async_migrate_entry
    VERSION = 1
    MINOR_VERSION = 3

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SecvestOptionsFlow:
        """Change the selection and the settings of a panel."""
        return SecvestOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Zone groups are subentries of the panel's entry."""
        return {SUBENTRY_ZONE_GROUP: ZoneGroupFlow}

    def __init__(self) -> None:
        """Start without a checked connection."""
        self._data: dict[str, Any] = {}
        self._title = ""
        self._partitions: list[Partition] = []
        self._zones: dict[str, Zone] = {}
        self._selected: list[int] = []

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
                error = await self._validate(data, read_partitions=True)
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
                self._selected = selected
                return await self.async_step_zones()
            errors["base"] = "no_partitions"
        # the panel doesn't reveal the user's rights, so all partitions are
        # offered; those without zones are deselected by default
        default = [str(p.number) for p in self._partitions if p.zone_ids]
        schema = vol.Schema(
            {
                vol.Required(CONF_PARTITIONS, default=default): partition_selector(
                    self._partitions
                )
            }
        )
        return self.async_show_form(
            step_id="partitions", data_schema=schema, errors=errors
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exclude zones and choose a device class per zone, as in the options."""
        partitions = {p.number: p for p in self._partitions}
        zones = zone_labels(partitions, self._zones, self._selected)
        if user_input is not None or not zones:
            excluded, classes = zone_options(zones, user_input or {}, {})
            return self.async_create_entry(
                title=self._title,
                data=self._data,
                options={
                    CONF_PARTITIONS: self._selected,
                    CONF_EXCLUDED_ZONES: excluded,
                    CONF_ZONE_DEVICE_CLASSES: classes,
                    CONF_CODES: [],
                },
            )
        return self.async_show_form(
            step_id="zones", data_schema=zone_schema(zones, {}, [])
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start after the panel rejected the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and check them with exactly one request."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                **entry.data,
                CONF_USER_CODE: user_input[CONF_USER_CODE].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            error = await self._validate(data, read_partitions=False)
            if error is None:
                # the reload creates a new transport with these credentials
                updates = {
                    CONF_USER_CODE: data[CONF_USER_CODE],
                    CONF_PASSWORD: data[CONF_PASSWORD],
                    CONF_AUTH_FAILED: False,
                }
                if entry.update_listeners:
                    # loaded (401 while running): its update listener reloads
                    # once the flag is cleared, as Home Assistant expects
                    return self.async_update_and_abort(entry, data_updates=updates)
                # not loaded (401 at setup, or after a restart): nothing else
                # would reload it
                return self.async_update_reload_and_abort(entry, data_updates=updates)
            errors["base"] = error
        # the password is never suggested
        user_code = (user_input or entry.data)[CONF_USER_CODE]
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_SCHEMA, {CONF_USER_CODE: user_code}
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def _validate(
        self, data: Mapping[str, Any], *, read_partitions: bool
    ) -> str | None:
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
            if read_partitions:
                self._partitions = await client.get_partitions()
                # for the zones step; only partitions that have zones, and on
                # the same connection
                for partition in self._partitions:
                    if partition.zone_ids:
                        for zone in await client.get_zones(partition.number):
                            self._zones.setdefault(zone.id, zone)
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
