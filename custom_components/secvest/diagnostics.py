"""Redacted diagnostics download (#43).

Everything that tells a panel's behaviour: the raw answers of the last
round, the state of the connection, the polling and the log, and the
tested firmware (ADR 0005). Left out or redacted is everything that
identifies the installation or its people: the address, credentials and
codes, and all names and texts (installation, partitions, zones, zone
groups, users, fault and log texts). Ids, states and types stay.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import SecvestConfigEntry
from .api.parsing import panel_time
from .codes import CONF_KDF, KDF
from .const import (
    CONF_CODES,
    CONF_INSTALLATION_NAME,
    CONF_USER_CODE,
    TESTED_FIRMWARE,
    TESTED_MODEL,
)

# keys anywhere in the entry and the panel's answers whose values identify
# the installation or people
TO_REDACT = {
    CONF_URL,
    CONF_USER_CODE,
    CONF_PASSWORD,
    CONF_INSTALLATION_NAME,
    CONF_NAME,
    "title",
    # the panel's answers: names and texts, which can name zones and rooms
    "desc",
    "ui-string",
    "username",
    "text",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SecvestConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics of a config entry; nothing is sent to the panel."""
    options = dict(entry.options)
    # how many codes and how they are hashed, never names, salts or hashes
    options[CONF_CODES] = [
        {CONF_KDF: code.get(CONF_KDF, KDF)} for code in options.get(CONF_CODES, [])
    ]
    diagnostics: dict[str, Any] = {
        "tested": {"model": TESTED_MODEL, "firmware": TESTED_FIRMWARE},
        "entry": async_redact_data(
            {
                "title": entry.title,
                "version": f"{entry.version}.{entry.minor_version}",
                "state": str(entry.state),
                "data": dict(entry.data),
                "options": options,
                "subentries": [
                    {
                        "type": subentry.subentry_type,
                        "title": subentry.title,
                        "data": dict(subentry.data),
                    }
                    for subentry in entry.subentries.values()
                ],
            },
            TO_REDACT,
        ),
    }
    if entry.state is not ConfigEntryState.LOADED:
        # e.g. after a 401: the stored settings only
        return diagnostics
    coordinator = entry.runtime_data
    transport = coordinator.client.transport
    log = coordinator.log
    newest = log.newest
    diagnostics.update(
        {
            "transport": {
                **asdict(transport.stats),
                "authentication_failed": transport.authentication_failed,
            },
            "polling": {
                "available": coordinator.available,
                "last_update_success": coordinator.last_update_success,
                "update_interval": (
                    coordinator.update_interval.total_seconds()
                    if coordinator.update_interval
                    else None
                ),
                "backoff": coordinator.backoff.as_dict(),
                "installer_locked": coordinator.installer_locked,
                "selected_partitions": list(coordinator.selected_partitions),
            },
            # the log's state, without its entries: their texts and user
            # names identify people
            "log": {
                "has_baseline": log.has_baseline,
                "newest_timestamp": newest,
                "newest_time": _panel_time(newest),
                "known_entries": len(log.as_dict()["known"]),
                "interval": coordinator.log_interval,
                "last_read_failed": coordinator.log_read_failed,
            },
            "responses": async_redact_data(
                dict(sorted(transport.last_responses.items())), TO_REDACT
            ),
        }
    )
    return diagnostics


def _panel_time(timestamp: int | None) -> str | None:
    """Return a panel timestamp in Home Assistant's time zone (#8)."""
    if not timestamp:
        return None
    return panel_time(timestamp, dt_util.get_default_time_zone()).isoformat()
