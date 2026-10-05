"""Subentry flow for zone groups (#67)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_DEVICE_CLASS, CONF_NAME
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import voluptuous as vol

from .const import (
    CONF_AREA_ID,
    CONF_HIDE_MEMBERS,
    CONF_ZONES,
    DOMAIN,
    ZONE_DEVICE_CLASSES,
)
from .flow_helpers import zone_labels
from .groups import SAME_AS_ZONES, zone_groups, zones_device_class


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
