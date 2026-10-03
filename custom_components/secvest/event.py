"""The panel's log as an event entity (#34)."""

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import LogEntry, LogType
from .const import DOMAIN
from .entity import SecvestEntity
from .log import entry_time

# the entity only receives what the coordinator read
PARALLEL_UPDATES = 0

# fired with every new log entry, so the logbook can show its text
EVENT_LOG_ENTRY = f"{DOMAIN}_log_entry"

# a log type the client doesn't know (logged once when parsed)
UNKNOWN = "unknown"
EVENT_TYPES = [*(log_type.value for log_type in LogType), UNKNOWN]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the log entity of a panel."""
    async_add_entities([LogEventEntity(entry.runtime_data, "log")])


def log_attributes(entry: LogEntry) -> dict[str, Any]:
    """Return what an entry tells, for the event and the logbook.

    The text is only displayed; no logic depends on it (principle 4).
    """
    event = entry.events[0] if entry.events else None
    written = entry_time(entry)
    return {
        "text": entry.text,
        "time": written.isoformat() if written else None,
        "user": event.user if event else None,
        "user_name": event.username if event else None,
        "partition": event.partition if event else None,
        "zone": event.zone_id if event else None,
    }


class LogEventEntity(SecvestEntity, EventEntity):
    """Fires once for each new entry of the panel's log.

    The entries come from the coordinator's incremental log reads: the
    baseline fires nothing, and the stored log state keeps a restart from
    replaying entries (#11).
    """

    _attr_translation_key = "log"
    _attr_event_types = EVENT_TYPES

    async def async_added_to_hass(self) -> None:
        """Receive the new log entries."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_log_listener(self._handle_log_entries)
        )

    @callback
    def _handle_log_entries(self, entries: list[LogEntry]) -> None:
        """Fire each entry, oldest first, and pass it on to the logbook."""
        for entry in entries:
            attributes = log_attributes(entry)
            event_type = str(entry.type) if entry.type in set(LogType) else UNKNOWN
            self._trigger_event(event_type, attributes)
            self.async_write_ha_state()
            self.hass.bus.async_fire(
                EVENT_LOG_ENTRY,
                {"entity_id": self.entity_id, "type": event_type, **attributes},
            )
