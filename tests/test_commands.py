"""Tests for commands and their verification (#12), on the coordinator."""

import asyncio

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
import pytest

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.api.errors import (
    ArmingBlockedError,
    AuthenticationError,
    ConnectionLostError,
    InstallerLockedError,
)
from custom_components.secvest.api.models import PartitionState
from custom_components.secvest.const import CONF_AUTH_FAILED, DOMAIN
from custom_components.secvest.coordinator import PanelState, SecvestCoordinator

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel, Injection

PUT = ("PUT", "/system/partitions-1/")


def _is(state: PartitionState):  # type: ignore[no-untyped-def]
    def reached(panel_state: PanelState) -> bool:
        return panel_state.partitions[1].state == state

    return reached


async def _arm(
    coordinator: SecvestCoordinator, state: PartitionState = PartitionState.SET
) -> coordinator_module.CommandOutcome:
    client = coordinator.client
    async with client.hold(priority=True):
        return await coordinator.async_command(
            lambda: client.set_partition_state(1, state), _is(state)
        )


async def test_command_is_verified(fake_panel: FakePanel, setup: Setup) -> None:
    """After the command the state is read again and decides."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    outcome = await _arm(coordinator)
    assert outcome.reached
    assert outcome.error is None
    assert not outcome.resent
    assert fake_panel.stats.requests == [*ROUND, PUT, *ROUND]
    # the entities see the verified state
    assert coordinator.data.partitions[1].state == PartitionState.SET


async def test_error_answer_is_verified_too(
    fake_panel: FakePanel, setup: Setup
) -> None:
    """A refusal is verified like any answer; the fresh state decides."""
    entry = await setup()
    fake_panel.open_zone("209")
    await coordinator_of(entry).async_refresh()
    outcome = await _arm(coordinator_of(entry))
    assert not outcome.reached
    assert isinstance(outcome.error, ArmingBlockedError)
    assert fake_panel.stats.requests[-5:] == [PUT, *ROUND]


async def test_reached_despite_error(fake_panel: FakePanel, setup: Setup) -> None:
    """If the target was reached anyway, the command succeeded."""
    entry = await setup()
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "status", status=500))
    # someone armed at the keypad at the same moment
    fake_panel.partitions[1].state = "set"
    outcome = await _arm(coordinator_of(entry))
    assert outcome.reached
    assert outcome.error is not None


async def test_lost_after_sending_takes_effect(
    fake_panel: FakePanel, setup: Setup
) -> None:
    """The answer was lost, but the command took effect: not sent again."""
    entry = await setup()
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "drop_after"))
    outcome = await _arm(coordinator_of(entry))
    assert outcome.reached
    assert isinstance(outcome.error, ConnectionLostError)
    assert not outcome.resent
    assert fake_panel.stats.requests.count(PUT) == 1


async def test_lost_before_it_took_effect_is_sent_once_more(
    fake_panel: FakePanel, setup: Setup
) -> None:
    """The single automatic retry: only after a lost connection, verified."""
    entry = await setup()
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "drop_before"))
    outcome = await _arm(coordinator_of(entry))
    assert outcome.reached
    assert outcome.resent
    assert outcome.error is None
    assert fake_panel.stats.requests[len(ROUND) :] == [PUT, *ROUND, PUT, *ROUND]


async def test_sent_once_more_at_most(fake_panel: FakePanel, setup: Setup) -> None:
    """A second lost connection isn't retried."""
    entry = await setup()
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "drop_before", times=2))
    outcome = await _arm(coordinator_of(entry))
    assert not outcome.reached
    assert isinstance(outcome.error, ConnectionLostError)
    assert fake_panel.stats.requests.count(PUT) == 2


async def test_401_ends_the_command(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """After a 401 nothing is sent, not even the verification."""
    entry = await setup()
    fake_panel.password = "changed"
    with pytest.raises(AuthenticationError):
        await _arm(coordinator_of(entry))
    await hass.async_block_till_done()
    assert fake_panel.stats.requests == [*ROUND, PUT]
    assert entry.data[CONF_AUTH_FAILED] is True
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_installer_lock_ends_the_command(
    fake_panel: FakePanel, setup: Setup
) -> None:
    """With the installer lock every request fails; no verification."""
    entry = await setup()
    fake_panel.installer_locked = True
    with pytest.raises(InstallerLockedError):
        await _arm(coordinator_of(entry))
    assert fake_panel.stats.requests == [*ROUND, PUT]
    assert coordinator_of(entry).installer_locked


async def test_verification_replaces_the_next_round(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next round keeps the minimum spacing from the verification."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    await _arm(coordinator)
    verified = hass.data[DOMAIN]["round_starts"][entry.entry_id]
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 0.5)
    await coordinator.async_refresh()
    assert hass.data[DOMAIN]["round_starts"][entry.entry_id] - verified >= 0.5


async def test_overtaken_round_is_discarded(
    fake_panel: FakePanel, setup: Setup
) -> None:
    """A round that read before a command doesn't overwrite its result."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.inject(Injection("GET", "/alarms/", "slow", delay=0.3))
    polling = asyncio.create_task(coordinator.async_refresh())
    # the round reads the partitions, then waits for /alarms/
    await asyncio.sleep(0.1)
    assert ("GET", "/alarms/") in fake_panel.stats.requests[len(ROUND) :]
    outcome = await _arm(coordinator)
    await polling
    assert outcome.reached
    assert coordinator.data.partitions[1].state == PartitionState.SET
