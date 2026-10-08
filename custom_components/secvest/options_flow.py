"""Options flow: settings and zones, codes, and names taken over from the panel."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_CODE, CONF_NAME
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api.errors import (
    AuthenticationError,
    CertificateError,
    InstallerLockedError,
    SecvestError,
)
from .codes import CODE_PATTERN, async_prepare, code_of
from .const import (
    CONF_ADVANCED,
    CONF_CODES,
    CONF_EXCLUDED_ZONES,
    CONF_INSTALLATION_NAME,
    CONF_LOG_INTERVAL,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    CONF_USER_AGENT,
    CONF_ZONE_DEVICE_CLASSES,
    DEFAULT_LOG_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    MAX_LOG_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_LOG_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .flow_helpers import partition_selector, zone_labels, zone_options, zone_schema
from .schema import vol


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
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "codes", "names"]
        )

    async def async_step_names(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take over the names from the panel, the installation's too (#137).

        The one step of the options that sends something: GET /system/ once,
        like the app at its start. Partitions and zones follow by themselves;
        the reload afterwards shows all names at once.
        """
        entry = self.config_entry
        if entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                name = await entry.runtime_data.async_read_installation_name()
            except CertificateError:
                # the confirmation of the certificate has started (#149)
                return self.async_abort(reason="certificate_changed")
            except AuthenticationError:
                # the reauthentication has started
                return self.async_abort(reason="auth_failed")
            except InstallerLockedError:
                errors["base"] = "installer_locked"
            except SecvestError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_INSTALLATION_NAME: name}
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data=dict(entry.options))
        return self.async_show_form(
            step_id="names", data_schema=vol.Schema({}), errors=errors
        )

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
