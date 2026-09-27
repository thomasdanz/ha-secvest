"""Tests for setup and the status polling round (#10)."""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.api.errors import CommunicationError
from custom_components.secvest.api.models import ZoneState
from custom_components.secvest.const import (
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    CONF_USER_AGENT,
)
from custom_components.secvest.coordinator import (
    Backoff,
    SecvestCoordinator,
    scan_interval,
)

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel, Injection

MANIFEST = Path(__file__).parent.parent / "custom_components/secvest/manifest.json"


def _available(coordinator: SecvestCoordinator) -> bool:
    # a function call, so that mypy doesn't narrow the property
    return coordinator.available


async def test_first_round_at_setup(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Setup runs one round; the state holds the selected partitions' zones."""
    fake_panel.open_zone("209")
    entry = await setup()
    assert entry.state is ConfigEntryState.LOADED
    assert fake_panel.stats.requests == ROUND
    assert fake_panel.stats.connections == 1
    state = coordinator_of(entry).data
    assert list(state.partitions) == [1, 2, 3, 4]
    assert len(state.zones) == len(fake_panel.partitions[1].zone_ids)
    assert state.zones["209"].state is ZoneState.OPEN
    assert [fault.zone_id for fault in state.faults] == ["209"]
    assert state.alarms == ()
    with pytest.raises(TypeError):
        state.zones["x"] = state.zones["209"]  # type: ignore[index]


async def test_zone_in_several_partitions(fake_panel: FakePanel, setup: Setup) -> None:
    """Only the selected partitions' zones are read; shared zones once."""
    fake_panel.partitions[2].zone_ids.append("209")
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    assert [path for _, path in fake_panel.stats.requests][3:] == [
        "/system/partitions-1/zones/",
        "/system/partitions-2/zones/",
    ]
    zones = coordinator_of(entry).data.zones
    assert len(zones) == len(fake_panel.partitions[1].zone_ids)


@pytest.mark.parametrize(
    ("options", "seconds"),
    [({}, 30), ({CONF_SCAN_INTERVAL: 60}, 60), ({CONF_SCAN_INTERVAL: 10}, 24)],
)
def test_scan_interval(
    options: dict[str, Any], seconds: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interval defaults to 30 s and never goes below 24 s."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 24)
    assert scan_interval(options) == timedelta(seconds=seconds)


async def test_rounds_never_overlap(fake_panel: FakePanel, setup: Setup) -> None:
    """Concurrent refreshes run one after another."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    await asyncio.gather(coordinator.async_refresh(), coordinator.async_refresh())
    assert fake_panel.stats.requests == ROUND * 3
    assert fake_panel.stats.max_open_connections == 1


async def test_minimum_spacing(
    fake_panel: FakePanel, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A round never starts sooner than the minimum after the last one."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.5)
    entry = await setup()
    started = time.monotonic()
    await coordinator_of(entry).async_refresh()
    assert time.monotonic() - started >= 0.4
    assert fake_panel.stats.requests == ROUND * 2


async def test_spacing_survives_a_reload(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reload creates a new coordinator but keeps the spacing."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.5)
    entry = await setup()
    started = time.monotonic()
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert time.monotonic() - started >= 0.4
    assert fake_panel.stats.requests == ROUND * 2


async def test_requested_refresh(
    fake_panel: FakePanel, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A manual refresh (update_entity) keeps the minimum spacing too."""
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.5)
    entry = await setup()
    started = time.monotonic()
    await coordinator_of(entry).async_request_refresh()
    assert time.monotonic() - started >= 0.4
    assert fake_panel.stats.requests == ROUND * 2


async def test_failed_round(fake_panel: FakePanel, setup: Setup) -> None:
    """A failed round marks the data as stale and keeps the last state."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    state = coordinator.data
    fake_panel.inject(Injection("GET", "/alarms/", "drop_before", times=2))
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.data is state


async def test_rejected_credentials_at_setup(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 at setup stops the entry without a retry."""
    entry = await setup(password="wrong")
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert fake_panel.stats.requests == [("GET", "/system/partitions/")]


async def test_rejected_credentials_later(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 during polling stops the rounds; nothing is sent again."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.password = "changed"
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator.client.transport.authentication_failed
    await coordinator.async_refresh()
    assert fake_panel.stats.requests == [*ROUND, ("GET", "/system/partitions/")]


async def test_unreachable_at_setup(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An unreachable panel lets Home Assistant retry the setup later."""
    fake_panel.inject(Injection("GET", "/system/partitions/", "drop_before", times=2))
    entry = await setup()
    assert entry.state is ConfigEntryState.SETUP_RETRY


def test_backoff_sequence() -> None:
    """The delay doubles up to the maximum, then polling pauses."""
    backoff = Backoff()
    error = CommunicationError("timed out")
    delays = [backoff.failed(error, 30) for _ in range(6)]
    assert delays == [60, 120, 240, 300, 900, 900]
    assert backoff.paused
    assert backoff.as_dict() == {
        "consecutive_failures": 6,
        "paused": True,
        "last_error": "CommunicationError: timed out",
    }
    backoff.succeeded()
    assert backoff.as_dict() == {
        "consecutive_failures": 0,
        "paused": False,
        "last_error": None,
    }
    assert backoff.not_before == 0


async def test_backoff_and_pause(
    fake_panel: FakePanel, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failures back off, pause after several in a row, success resets."""
    monkeypatch.setattr(coordinator_module, "PAUSE_AFTER", 2)
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.inject(Injection("GET", "/alarms/", "drop_before", times=4))

    await coordinator.async_refresh()
    assert coordinator.backoff.failures == 1
    # a single failure keeps the last state and the entities available
    assert _available(coordinator)

    # a manual refresh doesn't shorten the backoff: nothing is sent
    sent = len(fake_panel.stats.requests)
    await coordinator.async_refresh()
    assert len(fake_panel.stats.requests) == sent
    assert coordinator.backoff.failures == 1

    coordinator.backoff.not_before = 0
    await coordinator.async_refresh()
    assert coordinator.backoff.paused
    assert not _available(coordinator)

    coordinator.backoff.not_before = 0
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert coordinator.backoff.failures == 0
    assert _available(coordinator)


async def test_retry_after_is_the_backoff(fake_panel: FakePanel, setup: Setup) -> None:
    """The next round is scheduled after the backoff delay."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.inject(Injection("GET", "/alarms/", "drop_before", times=2))
    await coordinator.async_refresh()
    assert isinstance(coordinator.last_exception, UpdateFailed)
    assert coordinator.last_exception.retry_after == 60


@pytest.mark.parametrize(
    ("stored", "expected"), [("", "ha-secvest/{version}"), ("Proxy/1", "Proxy/1")]
)
async def test_user_agent_in_every_request(
    fake_panel: FakePanel, setup: Setup, stored: str, expected: str
) -> None:
    """Polling sends the stored override, or ha-secvest/<manifest version>."""
    version = json.loads(MANIFEST.read_text())["version"]
    entry = await setup(data={CONF_USER_AGENT: stored})
    await coordinator_of(entry).async_refresh()
    assert fake_panel.stats.user_agents == [expected.format(version=version)] * (
        2 * len(ROUND)
    )
