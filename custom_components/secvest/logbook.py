"""Log entries of the panel in Home Assistant's logbook (#34).

The log event entity's own logbook rows only show the type of an entry, so
each entry is described once more with its text.
"""

from collections.abc import Callable

from homeassistant.components.logbook.const import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .event import EVENT_LOG_ENTRY


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[
        [str, str, Callable[[Event], dict[str, str | None]]], None
    ],
) -> None:
    """Describe the log entries of the panel."""

    @callback
    def async_describe_log_entry(event: Event) -> dict[str, str | None]:
        entity_id = event.data.get(ATTR_ENTITY_ID)
        state = hass.states.get(entity_id) if entity_id else None
        return {
            LOGBOOK_ENTRY_NAME: state.name if state else "Secvest",
            LOGBOOK_ENTRY_MESSAGE: _message(event),
            LOGBOOK_ENTRY_ENTITY_ID: entity_id,
        }

    async_describe_event(DOMAIN, EVENT_LOG_ENTRY, async_describe_log_entry)


def _message(event: Event) -> str:
    """Return the entry's text with the panel's time of it.

    The log is read every few minutes, so the logbook row comes later than
    the entry; the date is added when it isn't today. Only the event's data
    is used: the logbook passes a partial event read from the database,
    without the time it was fired (#167).
    """
    text = str(event.data.get("text") or "")
    if (written := dt_util.parse_datetime(event.data.get("time") or "")) is None:
        return text
    written = dt_util.as_local(written)
    stamp = f"{written:%H:%M:%S}"
    if written.date() != dt_util.now().date():
        stamp = f"{written:%Y-%m-%d} {stamp}"
    return f"{text} ({stamp})"
