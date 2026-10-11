"""Repair flows: confirming that the web interface may log in again (#56)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import (
    ConfirmRepairFlow,
    RepairsFlow,
    RepairsFlowResult,
)
from homeassistant.core import HomeAssistant

from .const import CONF_WEB_LOGIN_FAILED


class WebLoginRepairFlow(ConfirmRepairFlow):
    """Allow one more web login with the stored credentials, once confirmed.

    Failed web logins count towards a code tamper alarm, so the integration
    never tries again on its own; the user confirms after checking the
    credentials (e.g. the password works at the web interface in a browser).
    """

    def __init__(self, entry_id: str) -> None:
        """Remember the entry whose web login failed."""
        super().__init__()
        self._entry_id = entry_id

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Clear the flag and reload, so the next read logs in once more."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_WEB_LOGIN_FAILED: False}
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the flow for a fixable issue; only the web login has one."""
    entry_id = str((data or {}).get("entry_id", ""))
    return WebLoginRepairFlow(entry_id)
