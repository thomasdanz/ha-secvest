"""Incremental log polling without gaps (#11)."""

import logging
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.api.models import LogEntry, LogEvent, LogType
from custom_components.secvest.log import OVERLAP, LogTracker

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel, Injection

FULL_LOG = ("GET", "/logs/")


def _entry(
    id_: str, timestamp: int, text: str = "Ben 003 TB 1 aktiv", **event: Any
) -> LogEntry:
    return LogEntry(
        id=id_,
        type=LogType.NORMAL,
        text=text,
        events=(
            LogEvent(
                timestamp=timestamp,
                partition=event.get("partition"),
                zone_id=event.get("zone_id"),
                user=event.get("user"),
                username=event.get("username"),
                images=(),
            ),
        ),
    )


# the tracker on its own


def test_baseline_fires_nothing() -> None:
    """The full log is the baseline; the overlap read later is all known."""
    tracker = LogTracker()
    assert not tracker.has_baseline
    log = [_entry("3", 3000), _entry("1", 1000), _entry("2", 2000)]
    tracker.baseline(log)
    assert tracker.newest == 3000
    assert tracker.since() == 0
    assert tracker.take(log) == []


def test_same_second() -> None:
    """An entry written later within the same second is new."""
    tracker = LogTracker()
    first = _entry("768", 5000)
    tracker.baseline([first])
    later = _entry("769", 5000, "Ben 003 TB 2 aktiv")
    # the panel lists the newest first
    assert tracker.take([later, first]) == [later]
    assert tracker.take([later, first]) == []


def test_order_oldest_first() -> None:
    """New entries come oldest first; within a second, as written."""
    tracker = LogTracker()
    tracker.baseline([_entry("1", 1000)])
    a, b, c = _entry("a", 2000), _entry("b", 2000, "second"), _entry("c", 3000)
    assert tracker.take([c, b, a]) == [a, b, c]


def test_clock_goes_back() -> None:
    """When daylight saving time ends, ids repeat; the content decides."""
    tracker = LogTracker()
    before = _entry("1792800000", 7000, "Ben 003 TB 1 aktiv")
    tracker.baseline([before])
    # the same local second again, an hour later in real time
    again = _entry("1792800000", 7000, "Ben 003 TB 1 deaktiv")
    assert tracker.take([again, before]) == [again]
    # and an entry an hour "earlier" is still within the overlap
    repeated_hour = _entry("x", 7000 - OVERLAP + 1)
    assert tracker.take([repeated_hour]) == [repeated_hour]


def test_overlap_window_pruned() -> None:
    """Only entries within the overlap are remembered."""
    tracker = LogTracker()
    tracker.baseline([_entry("old", 1000), _entry("new", 1000 + OVERLAP + 10)])
    assert len(tracker.as_dict()["known"]) == 1
    assert tracker.since() == 1010


def test_stored_state_round_trip() -> None:
    """The stored state reads back equal; an unreadable one means a baseline."""
    tracker = LogTracker()
    log = [_entry("1", 5000, partition=0, user=3, username="User", zone_id="209")]
    tracker.baseline(log)
    restored = LogTracker.from_dict(tracker.as_dict())
    assert restored.newest == 5000
    assert restored.take(log) == []
    assert not LogTracker.from_dict({"newest": "x", "known": []}).has_baseline
    assert not LogTracker.from_dict({"known": []}).has_baseline


# with the fake panel


@pytest.fixture
def log_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the log with the first round instead of a minute later."""
    monkeypatch.setattr(coordinator_module, "FIRST_LOG_DELAY", 0)


def _batches(entry: MockConfigEntry) -> list[list[LogEntry]]:
    batches: list[list[LogEntry]] = []
    coordinator_of(entry).async_add_log_listener(batches.append)
    return batches


async def _poll_now(entry: MockConfigEntry) -> None:
    coordinator = coordinator_of(entry)
    coordinator._log_due = 0
    await coordinator.async_refresh()


def _log_requests(fake_panel: FakePanel) -> list[str]:
    return [path for _, path in fake_panel.stats.requests if path.startswith("/logs/")]


async def test_baseline_then_incremental(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """The full log once, then filtered reads; each new entry once."""
    entry = await setup()
    assert fake_panel.stats.requests == [FULL_LOG, *ROUND]
    batches = _batches(entry)

    written = fake_panel.add_log_entry(
        "Ben 003 TB 1 aktiv", partition="0", user="3", username="User"
    )
    await _poll_now(entry)
    assert _log_requests(fake_panel)[-1].startswith("/logs/?$filter=timestamp%20ge%20")
    assert len(batches) == 1
    (new,) = batches[0]
    assert new.id == written["id"]
    assert new.events[0].partition == 1

    # read again: nothing new, nothing fired
    await _poll_now(entry)
    assert len(batches) == 1


async def test_log_interval(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """Rounds before the log interval is over don't read the log."""
    entry = await setup()
    await coordinator_of(entry).async_refresh()
    await coordinator_of(entry).async_refresh()
    assert fake_panel.stats.requests == [FULL_LOG, *ROUND, *ROUND, *ROUND]


async def test_first_read_after_setup(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without the test shortcut, setup doesn't wait for the slow log."""
    await setup()
    assert _log_requests(fake_panel) == []


async def test_restart_neither_replays_nor_skips(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """The stored state survives a reload; no new baseline, no replay."""
    entry = await setup()
    fake_panel.add_log_entry("before the restart")
    await _poll_now(entry)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # the first round after the restart reads incrementally, firing nothing
    assert _log_requests(fake_panel).count("/logs/") == 1
    batches = _batches(entry)
    await _poll_now(entry)
    assert batches == []
    # the stored state was used: no second full read
    assert _log_requests(fake_panel).count("/logs/") == 1
    written = fake_panel.add_log_entry("after the restart")
    await _poll_now(entry)
    assert [[e.id for e in batch] for batch in batches] == [[written["id"]]]


async def test_clock_goes_back_at_the_panel(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, log_now: None
) -> None:
    """An entry an hour 'earlier' after the clock went back still fires."""
    entry = await setup()
    fake_panel.add_log_entry("summer time")
    await _poll_now(entry)
    batches = _batches(entry)
    fake_panel.clock_offset -= OVERLAP
    written = fake_panel.add_log_entry("winter time")
    await _poll_now(entry)
    assert [[e.id for e in batch] for batch in batches] == [[written["id"]]]


async def test_failed_log_read_keeps_the_round(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    log_now: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing log read doesn't fail the round; it is logged once."""
    fake_panel.inject(Injection("GET", "/logs/", "status", status=500, times=2))
    entry = await setup()
    coordinator = coordinator_of(entry)
    assert coordinator.last_update_success
    assert not coordinator.log.has_baseline
    await _poll_now(entry)
    assert coordinator.last_update_success
    assert caplog.text.count("Reading the log failed") == 1
    await _poll_now(entry)
    assert coordinator.log.has_baseline


async def test_log_state_removed_with_the_entry(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    log_now: None,
    hass_storage: dict[str, Any],
) -> None:
    """Removing the entry removes its log state."""
    entry = await setup()
    key = f"secvest.log.{entry.entry_id}"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_rejected_credentials_at_the_log(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    log_now: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 401 at the log read ends the round like any request."""
    caplog.set_level(logging.WARNING)
    entry = await setup()
    fake_panel.inject(Injection("GET", "/logs/", "status", status=401))
    await _poll_now(entry)
    assert not coordinator_of(entry).last_update_success
    assert coordinator_of(entry).client.transport.authentication_failed
