"""Config flow for the ABUS Secvest integration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    CONF_CODE,
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_URL,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
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
from .api.models import Partition, Zone
from .api.transport import Transport
from .codes import CODE_PATTERN, async_prepare, code_of
from .const import (
    CONF_ADVANCED,
    CONF_AREA_ID,
    CONF_AUTH_FAILED,
    CONF_CODES,
    CONF_EXCLUDED_ZONES,
    CONF_HIDE_MEMBERS,
    CONF_LOG_INTERVAL,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    CONF_ZONE_DEVICE_CLASSES,
    CONF_ZONES,
    DEFAULT_LOG_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_LOG_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_LOG_INTERVAL,
    MIN_SCAN_INTERVAL,
    SUBENTRY_ZONE_GROUP,
    TESTED_FIRMWARE,
    TESTED_MODEL,
    ZONE_DEVICE_CLASSES,
)
from .groups import SAME_AS_ZONES, zone_groups, zones_device_class

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


def partition_selector(partitions: Iterable[Partition]) -> SelectSelector:
    """Offer partitions for selection, labelled with number and name."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=str(p.number), label=f"{p.number}: {p.name}")
                for p in partitions
            ],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


def zone_labels(
    partitions: Mapping[int, Partition],
    zones: Mapping[str, Zone],
    selected: Iterable[int],
) -> list[tuple[str, str]]:
    """Return (zone id, label) for the zones of the selected partitions.

    The labels are the field names of the zones step, since the zones are
    the panel's and have no translations.
    """
    zone_ids = {
        zone_id
        for number in selected
        if (partition := partitions.get(number)) is not None
        for zone_id in partition.zone_ids
    }
    labels = []
    for zone_id in sorted(zone_ids, key=lambda value: (len(value), value)):
        # zones not read yet (a newly selected partition in the options) are
        # only known by their id
        zone = zones.get(zone_id)
        name = zone.name if zone is not None else "Zone"
        labels.append((zone_id, f"{name} ({zone_id})"))
    return labels


def zone_schema(
    zones: list[tuple[str, str]],
    classes: Mapping[str, str],
    excluded: Iterable[str],
) -> vol.Schema:
    """Build the zones step: excluded zones and a device class per zone."""
    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_EXCLUDED_ZONES,
            default=[zone_id for zone_id in excluded if zone_id in dict(zones)],
        ): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=zone_id, label=label)
                    for zone_id, label in zones
                ],
                multiple=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    }
    device_classes = SelectSelector(
        SelectSelectorConfig(
            options=["none", *ZONE_DEVICE_CLASSES],
            translation_key="device_class",
            mode=SelectSelectorMode.DROPDOWN,
        )
    )
    for zone_id, label in zones:
        default = classes.get(zone_id, "none")
        fields[vol.Required(label, default=default)] = device_classes
    return vol.Schema(fields)


def zone_options(
    zones: list[tuple[str, str]],
    user_input: Mapping[str, Any],
    previous: Mapping[str, str],
) -> tuple[list[str], dict[str, str]]:
    """Return the excluded zones and the device classes from the zones step.

    Classes of zones that aren't shown, e.g. of a partition deselected for
    now, are kept.
    """
    shown = {zone_id for zone_id, _ in zones}
    excluded = [
        zone_id
        for zone_id, _ in zones
        if zone_id in user_input.get(CONF_EXCLUDED_ZONES, [])
    ]
    classes = {
        zone_id: device_class
        for zone_id, device_class in previous.items()
        if zone_id not in shown
    }
    for zone_id, label in zones:
        if (device_class := user_input.get(label, "none")) != "none":
            classes[zone_id] = device_class
    return excluded, classes


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


class SecvestOptionsFlow(OptionsFlow):
    """Change partitions, zones and settings, or manage the codes.

    Settings reload the entry afterwards, codes don't (#141). Nothing is
    sent to the panel: the choices come from the last polling round.
    """

    def __init__(self) -> None:
        """Start with the current options."""
        self._options: dict[str, Any] = {}
        self._user_agent: str | None = None
        # the user whose code is being changed
        self._code_name: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose between the settings and the codes."""
        return self.async_show_menu(step_id="init", menu_options=["settings", "codes"])

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the partitions and the polling interval."""
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        state = entry.runtime_data.data
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = sorted(int(value) for value in user_input[CONF_PARTITIONS])
            if selected:
                self._options = {
                    **entry.options,
                    CONF_PARTITIONS: selected,
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_LOG_INTERVAL: int(user_input[CONF_LOG_INTERVAL]),
                }
                user_agent = user_input[CONF_ADVANCED].get(CONF_USER_AGENT, "").strip()
                if user_agent != entry.data.get(CONF_USER_AGENT, ""):
                    self._user_agent = user_agent
                return await self.async_step_zones()
            errors["base"] = "no_partitions"
        schema = vol.Schema(
            {
                vol.Required(CONF_PARTITIONS): partition_selector(
                    state.partitions.values()
                ),
                vol.Required(CONF_SCAN_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_LOG_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_LOG_INTERVAL,
                        max=MAX_LOG_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_ADVANCED): section(
                    vol.Schema({vol.Optional(CONF_USER_AGENT): TextSelector()}),
                    {"collapsed": True},
                ),
            }
        )
        current = {
            CONF_PARTITIONS: [str(n) for n in entry.options.get(CONF_PARTITIONS, [])],
            CONF_SCAN_INTERVAL: entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            ),
            CONF_LOG_INTERVAL: entry.options.get(
                CONF_LOG_INTERVAL, DEFAULT_LOG_INTERVAL
            ),
            CONF_ADVANCED: {CONF_USER_AGENT: entry.data.get(CONF_USER_AGENT, "")},
        }
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or current
            ),
            errors=errors,
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exclude zones and choose a device class per zone."""
        entry = self.config_entry
        state = entry.runtime_data.data
        zones = zone_labels(
            state.partitions, state.zones, self._options[CONF_PARTITIONS]
        )
        if user_input is not None or not zones:
            excluded, classes = zone_options(
                zones, user_input or {}, entry.options.get(CONF_ZONE_DEVICE_CLASSES, {})
            )
            options = {
                **self._options,
                CONF_EXCLUDED_ZONES: excluded,
                CONF_ZONE_DEVICE_CLASSES: classes,
            }
            # data and options in one update, so the entry reloads once
            data = dict(entry.data)
            if self._user_agent is not None:
                data = {**data, CONF_USER_AGENT: self._user_agent}
            self.hass.config_entries.async_update_entry(
                entry, data=data, options=options
            )
            return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="zones",
            data_schema=zone_schema(
                zones,
                entry.options.get(CONF_ZONE_DEVICE_CLASSES, {}),
                entry.options.get(CONF_EXCLUDED_ZONES, []),
            ),
        )

    # codes for arming and disarming (#116, #141): Home Assistant's own,
    # nothing is sent to the panel; only a salted hash is stored

    async def async_step_codes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add, change or remove a code."""
        menu = ["add_code"]
        if self.config_entry.options.get(CONF_CODES):
            menu += ["change_code", "remove_code"]
        return self.async_show_menu(step_id="codes", menu_options=menu)

    async def async_step_add_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a user's code."""
        return await self._code_form("add_code", user_input, None)

    async def async_step_change_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the user whose name or code changes."""
        if user_input is not None:
            self._code_name = user_input[CONF_NAME]
            return await self.async_step_edit_code()
        return self.async_show_form(
            step_id="change_code", data_schema=self._user_schema()
        )

    async def async_step_edit_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the chosen user's name or code."""
        return await self._code_form("edit_code", user_input, self._code_name)

    async def async_step_remove_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a user's code."""
        if user_input is not None:
            name = user_input[CONF_NAME]
            return self._save_codes(
                [c for c in self._stored_codes() if c[CONF_NAME] != name]
            )
        return self.async_show_form(
            step_id="remove_code", data_schema=self._user_schema()
        )

    def _stored_codes(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.config_entry.options.get(CONF_CODES, [])]

    def _user_schema(self) -> vol.Schema:
        names = [c[CONF_NAME] for c in self._stored_codes()]
        return vol.Schema(
            {
                vol.Required(CONF_NAME): SelectSelector(
                    SelectSelectorConfig(
                        options=names, mode=SelectSelectorMode.DROPDOWN
                    )
                )
            }
        )

    def _save_codes(self, stored: list[dict[str, Any]]) -> ConfigFlowResult:
        """Store the codes; this doesn't reload the entry."""
        return self.async_create_entry(
            data={**self.config_entry.options, CONF_CODES: stored}
        )

    async def _code_form(
        self, step_id: str, user_input: dict[str, Any] | None, name: str | None
    ) -> ConfigFlowResult:
        stored = self._stored_codes()
        current = next((c for c in stored if c[CONF_NAME] == name), None)
        others = [code_of(c) for c in stored if c is not current]
        errors: dict[str, str] = {}
        prepared: dict[str, Any] | None = None
        if user_input is not None:
            new_name = user_input[CONF_NAME].strip()
            code = (user_input.get(CONF_CODE) or "").strip()
            if not new_name:
                errors[CONF_NAME] = "name_required"
            elif new_name.casefold() in {c.name.casefold() for c in others}:
                errors[CONF_NAME] = "name_exists"
            elif (current is None or code) and not CODE_PATTERN.fullmatch(code):
                errors[CONF_CODE] = "invalid_code"
            # the comparison and the hash run in the executor (#139)
            elif (
                code
                and (prepared := await async_prepare(self.hass, others, code)) is None
            ):
                errors[CONF_CODE] = "code_exists"
            else:
                # an empty code keeps the current one
                changed = {
                    **(current or {}),
                    **(prepared or {}),
                    CONF_NAME: new_name,
                }
                if current is None:
                    return self._save_codes([*stored, changed])
                return self._save_codes(
                    [changed if c is current else c for c in stored]
                )
        code_field = (
            vol.Required(CONF_CODE) if current is None else vol.Optional(CONF_CODE)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                # when changing, an empty code keeps the current one
                code_field: TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        # the code is never suggested; it isn't stored
        suggested = (user_input or current or {}).get(CONF_NAME)
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_NAME: suggested}
            ),
            errors=errors,
        )


class ZoneGroupFlow(ConfigSubentryFlow):
    """Add or change a zone group; nothing is sent to the panel.

    The zones come from the last polling round.
    """

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a zone group."""
        return self._form("user", user_input, None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a zone group."""
        return self._form("reconfigure", user_input, self._get_reconfigure_subentry())

    def _form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        subentry: ConfigSubentry | None,
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        coordinator = entry.runtime_data
        state = coordinator.data
        own = subentry.subentry_id if subentry is not None else None
        others = [g for g in zone_groups(entry) if g.subentry_id != own]
        # a zone belongs to at most one group
        taken = {zone_id for group in others for zone_id in group.zone_ids}
        zones = [
            (zone_id, label)
            for zone_id, label in zone_labels(
                state.partitions, state.zones, coordinator.selected_partitions
            )
            if zone_id not in taken
        ]
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            chosen = [z for z, _ in zones if z in user_input.get(CONF_ZONES, [])]
            if not name:
                errors[CONF_NAME] = "name_required"
            elif name.casefold() in {g.name.casefold() for g in others}:
                errors[CONF_NAME] = "name_exists"
            elif len(chosen) < 2:
                errors[CONF_ZONES] = "too_few_zones"
            elif user_input[
                CONF_DEVICE_CLASS
            ] == SAME_AS_ZONES and not zones_device_class(self.hass, entry, chosen):
                errors[CONF_DEVICE_CLASS] = "zones_differ"
            else:
                data = {
                    CONF_NAME: name,
                    CONF_ZONES: chosen,
                    CONF_DEVICE_CLASS: user_input[CONF_DEVICE_CLASS],
                    CONF_HIDE_MEMBERS: user_input[CONF_HIDE_MEMBERS],
                    CONF_AREA_ID: user_input.get(CONF_AREA_ID),
                }
                if subentry is None:
                    return self.async_create_entry(title=name, data=data)
                # the device exists: set its area directly
                if (device := _group_device(self.hass, entry, subentry)) is not None:
                    dr.async_get(self.hass).async_update_device(
                        device.id, area_id=data[CONF_AREA_ID]
                    )
                # the entry reloads, since its subentries changed
                return self.async_update_and_abort(
                    entry, subentry, title=name, data=data
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                # optional, so the frontend doesn't preselect the first zone;
                # at least two are checked above
                vol.Optional(CONF_ZONES): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=zone_id, label=label)
                            for zone_id, label in zones
                        ],
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_DEVICE_CLASS): SelectSelector(
                    SelectSelectorConfig(
                        options=[SAME_AS_ZONES, *ZONE_DEVICE_CLASSES],
                        translation_key="device_class",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_HIDE_MEMBERS): bool,
                vol.Optional(CONF_AREA_ID): AreaSelector(),
            }
        )
        suggested: Mapping[str, Any]
        if user_input is not None:
            suggested = user_input
        elif subentry is not None:
            # the device's current area, which the device page may have changed
            device = _group_device(self.hass, entry, subentry)
            suggested = {
                **subentry.data,
                CONF_AREA_ID: device.area_id if device is not None else None,
            }
        else:
            suggested = {CONF_DEVICE_CLASS: SAME_AS_ZONES, CONF_HIDE_MEMBERS: False}
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )


def _group_device(
    hass: Any, entry: ConfigEntry, subentry: ConfigSubentry
) -> dr.DeviceEntry | None:
    """Return the device of a zone group, if it exists yet."""
    identifier = (DOMAIN, f"{entry.entry_id}_group_{subentry.subentry_id}")
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if identifier in device.identifiers and isinstance(device, dr.DeviceEntry):
            return device
    return None
