"""The log as an event entity, and its entries in the logbook (#34)."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.secvest.event import EVENT_LOG_ENTRY
from custom_components.secvest.logbook import async_describe_events

from .common import Setup, get_state, poll_log_now
from .fake_panel import FakePanel

LOG = "event.alarmanlage_log"


def _changes(hass: HomeAssistant) -> list[Event[Any]]:
    """Collect the log entity's state changes, i.e. its events."""
    changes: list[Event[Any]] = []

    @callback
    def _collect(event: Event[Any]) -> None:
        if event.data["entity_id"] == LOG:
            changes.append(event)

    hass.bus.async_listen("state_changed", _collect)
    return changes


async def test_new_entry_fires_once(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """Each new entry fires once with what it tells; the baseline nothing."""
    fake_panel.add_log_entry("written before the baseline")
    entry = await setup()
    assert get_state(hass, LOG).state == "unknown"
    changes = _changes(hass)
    bus_events = async_capture_events(hass, EVENT_LOG_ENTRY)

    fake_panel.add_log_entry(
        "Ben 003 TB 1 aktiv", partition="0", user="3", username="User", zone="209"
    )
    await poll_log_now(entry)
    await hass.async_block_till_done()
    state = get_state(hass, LOG)
    attributes = dict(state.attributes)
    time = dt_util.parse_datetime(attributes.pop("time"))
    assert time is not None
    assert time.tzinfo is not None
    assert attributes == {
        "event_type": "normal",
        "event_types": ["normal", "alarm", "trouble", "unknown"],
        "friendly_name": "Alarmanlage Log",
        "text": "Ben 003 TB 1 aktiv",
        "user": 3,
        "user_name": "User",
        "partition": 1,
        "zone": "209",
    }
    assert len(changes) == 1
    (bus_event,) = bus_events
    assert bus_event.data["entity_id"] == LOG
    assert bus_event.data["text"] == "Ben 003 TB 1 aktiv"

    # read again: the overlap returns the entry, but it fires nothing
    await poll_log_now(entry)
    await hass.async_block_till_done()
    assert len(changes) == 1
    assert len(bus_events) == 1


async def test_entries_fire_oldest_first(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """Several new entries in one read fire one after the other."""
    entry = await setup()
    changes = _changes(hass)
    fake_panel.add_log_entry("first")
    fake_panel.add_log_entry("Sabotage Z216", "alarm", zone="216")
    fake_panel.add_log_entry("Netzausfall", "trouble")
    await poll_log_now(entry)
    await hass.async_block_till_done()
    assert [
        (
            c.data["new_state"].attributes["event_type"],
            c.data["new_state"].attributes["text"],
        )
        for c in changes
    ] == [("normal", "first"), ("alarm", "Sabotage Z216"), ("trouble", "Netzausfall")]
    # every event is its own state change
    assert len({c.data["new_state"].state for c in changes}) == 3


async def test_unknown_log_type(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """A type the client doesn't know fires as unknown instead of failing."""
    entry = await setup()
    fake_panel.add_log_entry("something new", "maintenance")
    await poll_log_now(entry)
    await hass.async_block_till_done()
    assert get_state(hass, LOG).attributes["event_type"] == "unknown"


async def test_restart_keeps_the_last_event(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """A restart shows the last event again and replays nothing."""
    entry = await setup()
    fake_panel.add_log_entry("before the restart")
    await poll_log_now(entry)
    await hass.async_block_till_done()
    before = get_state(hass, LOG)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    changes = _changes(hass)
    await poll_log_now(entry)
    await hass.async_block_till_done()
    after = get_state(hass, LOG)
    assert (after.state, after.attributes["text"]) == (
        before.state,
        "before the restart",
    )
    assert changes == []


# the logbook


def _describer(hass: HomeAssistant) -> Callable[[Event[Any]], dict[str, str | None]]:
    described: dict[str, Callable[[Event[Any]], dict[str, str | None]]] = {}

    def _describe(
        domain: str, event_type: str, describe: Callable[[Event[Any]], Any]
    ) -> None:
        assert domain == "secvest"
        described[event_type] = describe

    async_describe_events(hass, _describe)
    return described[EVENT_LOG_ENTRY]


def _log_entry_event(time: str | None, fired: datetime) -> Event[Any]:
    return Event(
        EVENT_LOG_ENTRY,
        {"entity_id": LOG, "text": "Ben 003 TB 1 aktiv", "time": time},
        time_fired_timestamp=fired.timestamp(),
    )


async def test_logbook_shows_text_and_panel_time(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The logbook row names the log entity and shows text and panel time."""
    await setup()
    describe = _describer(hass)
    fired = dt_util.now().replace(hour=10, minute=5, second=0, microsecond=0)
    written = fired.replace(minute=1, second=23)
    assert describe(_log_entry_event(written.isoformat(), fired)) == {
        "name": "Alarmanlage Log",
        "message": "Ben 003 TB 1 aktiv (10:01:23)",
        "entity_id": LOG,
    }
    # written on another day, e.g. read after an outage: with the date
    yesterday = written - timedelta(days=1)
    message = describe(_log_entry_event(yesterday.isoformat(), fired))["message"]
    assert message == f"Ben 003 TB 1 aktiv ({yesterday:%Y-%m-%d} 10:01:23)"
    # without a time, the text alone
    assert describe(_log_entry_event(None, fired))["message"] == "Ben 003 TB 1 aktiv"
