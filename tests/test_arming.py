"""Tests for arming and disarming with verified success (#16, #17)."""

from pathlib import Path
from typing import Any

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.core import Event, HomeAssistant
import pytest

from custom_components.secvest.api.errors import ArmingBlockedError
from custom_components.secvest.api.models import PanelEvent, PartitionState
from custom_components.secvest.commands import (
    EVENT_ARMING_FAILED,
    CommandError,
    Failure,
    explain,
)
from custom_components.secvest.const import CONF_PARTITIONS
from custom_components.secvest.coordinator import CommandOutcome

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel, Injection

PANEL = "alarm_control_panel.alarmanlage_teilber_1"
CODE = "4711"
FIXTURES = Path(__file__).parent / "fixtures"


async def _call(hass: HomeAssistant, service: str, entity_id: str = PANEL) -> None:
    await hass.services.async_call(
        "alarm_control_panel",
        service,
        {"entity_id": entity_id, "code": CODE},
        blocking=True,
    )


def _state(hass: HomeAssistant, entity_id: str = PANEL) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


def _events(hass: HomeAssistant) -> list[Event[Any]]:
    events: list[Event[Any]] = []
    hass.bus.async_listen(EVENT_ARMING_FAILED, events.append)
    return events


@pytest.mark.parametrize(
    ("service", "sent", "expected"),
    [
        ("alarm_arm_away", "set", AlarmControlPanelState.ARMED_AWAY),
        ("alarm_arm_home", "partset", AlarmControlPanelState.ARMED_HOME),
    ],
)
async def test_arm(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    service: str,
    sent: str,
    expected: str,
) -> None:
    """Arming sends the state and shows the verified result."""
    await setup(code=CODE)
    await _call(hass, service)
    assert _state(hass) == expected
    assert fake_panel.partitions[1].state == sent
    assert fake_panel.stats.requests == [
        *ROUND,
        ("GET", "/system/partitions/"),
        ("PUT", "/system/partitions-1/"),
        *ROUND,
    ]


async def test_disarm(hass: HomeAssistant, fake_panel: FakePanel, setup: Setup) -> None:
    """Disarming is verified the same way."""
    entry = await setup(code=CODE)
    await _call(hass, "alarm_arm_away")
    await _call(hass, "alarm_disarm")
    assert _state(hass) == AlarmControlPanelState.DISARMED
    assert coordinator_of(entry).data.partitions[1].state == PartitionState.UNSET


async def test_blocked(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """409 names the blocking zones (certain); the event says the same."""
    await setup(code=CODE)
    events = _events(hass)
    fake_panel.open_zone("209")
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "arm_failed_blocked"
    assert err.value.translation_placeholders == {
        "partition": "Teilber. 1",
        "items": "Room 6 L",
    }
    assert str(err.value) == "Partition Teilber. 1 was not armed: blocked by Room 6 L"
    # the entity stays in its real state
    assert _state(hass) == AlarmControlPanelState.DISARMED
    await hass.async_block_till_done()
    assert [event.data for event in events] == [
        {
            "entry_id": events[0].data["entry_id"],
            "partition": 1,
            "partition_name": "Teilber. 1",
            "requested": "set",
            "reason": "blocked",
            "step": "command",
            "zones": ["209"],
            "faults": [],
        }
    ]


async def test_refused_without_reason(hass: HomeAssistant, setup: Setup) -> None:
    """409 with an empty list: a partition without zones."""
    await setup(code=CODE, **{CONF_PARTITIONS: [1, 2]})
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away", "alarm_control_panel.alarmanlage_teilber_2")
    assert err.value.translation_key == "arm_failed_refused"


async def test_no_permission(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An empty 403 means no permission for the partition (certain)."""
    await setup(code=CODE)
    await _call(hass, "alarm_arm_away")
    fake_panel.rights = {2}
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_disarm")
    assert err.value.translation_key == "disarm_failed_no_permission"
    assert str(err.value) == (
        "Partition Teilber. 1 was not disarmed: no permission for this partition"
    )
    assert _state(hass) == AlarmControlPanelState.ARMED_AWAY
    fake_panel.rights = None
    await _call(hass, "alarm_disarm")
    fake_panel.rights = {2}
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "arm_failed_no_permission"


async def test_silently_ignored(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """200 without effect: the likely reason comes from the fresh state."""
    await setup(code=CODE)
    events = _events(hass)
    # an open entry door makes the reference panel ignore "set"
    fake_panel.open_zone("219")
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "arm_failed_likely_open_zones"
    assert "probably" in str(err.value)
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "likely_open_zones"
    assert events[0].data["zones"] == ["219"]


async def test_installer_lock(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The installer lock has its own message; nothing is verified."""
    await setup(code=CODE)
    fake_panel.installer_locked = True
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "installer_locked"


async def test_not_verified(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If the result can't be read back, the message says so."""
    await setup(code=CODE)
    fake_panel.inject(Injection("GET", "/system/partitions/", "drop_before", times=2))
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "not_verified"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ArmingBlockedError(()), Failure("refused")),
        (
            ArmingBlockedError(
                (
                    PanelEvent("5000", "1514", "Z209", (1,), "209", True, False, False),
                    PanelEvent("1234", "7", "Sabotage", (1,), None, True, False, False),
                )
            ),
            Failure("blocked", ("209",), ("Sabotage",)),
        ),
    ],
)
async def test_explain_from_the_answer(
    fake_panel: FakePanel,
    setup: Setup,
    error: ArmingBlockedError,
    expected: Failure,
) -> None:
    """The reason from a 409 is certain and names zones and faults."""
    entry = await setup(code=CODE)
    outcome = CommandOutcome(False, error, coordinator_of(entry).data, False)
    assert explain(outcome, 1, PartitionState.SET) == expected


async def test_explain_likely_faults(fake_panel: FakePanel, setup: Setup) -> None:
    """Without open zones, blocking faults are the likely reason."""
    entry = await setup(code=CODE)
    fake_panel.static_faults.append(
        {
            "type": "1234",
            "id": "7",
            "ui-string": "Sabotage",
            "affects-partition": ["1"],
            "prevents-set": True,
            "prevents-reset": False,
            "is-rf-warning": False,
        }
    )
    await coordinator_of(entry).async_refresh()
    outcome = CommandOutcome(False, None, coordinator_of(entry).data, False)
    assert explain(outcome, 1, PartitionState.SET) == Failure(
        "likely_faults", faults=("Sabotage",)
    )
    # otherwise the reason is unknown; for disarming, nothing is derived
    assert explain(outcome, 1, PartitionState.UNSET) == Failure("unknown")


PUT = ("PUT", "/system/partitions-1/")
# the fresh read before deciding what to send
READ = ("GET", "/system/partitions/")


async def test_switch_modes(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Away → home disarms first; the entity doesn't show the step between."""
    await setup(code=CODE)
    await _call(hass, "alarm_arm_away")
    shown: list[str] = []
    hass.bus.async_listen(
        "state_changed",
        lambda event: (
            shown.append(event.data["new_state"].state)
            if event.data["entity_id"] == PANEL
            else None
        ),
    )
    sent = len(fake_panel.stats.requests)
    await _call(hass, "alarm_arm_home")
    await hass.async_block_till_done()
    assert _state(hass) == AlarmControlPanelState.ARMED_HOME
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND, PUT, *ROUND]
    assert AlarmControlPanelState.DISARMED not in shown


async def test_same_mode_sends_it_once(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming into the current mode doesn't disarm first."""
    await setup(code=CODE)
    await _call(hass, "alarm_arm_away")
    sent = len(fake_panel.stats.requests)
    await _call(hass, "alarm_arm_away")
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND]


async def test_switch_fails_at_disarming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed first step stops the sequence and is named."""
    await setup(code=CODE)
    events = _events(hass)
    await _call(hass, "alarm_arm_away")
    fake_panel.rights = {2}
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_home")
    assert err.value.translation_key == "switch_failed_no_permission"
    assert "disarming first failed" in str(err.value)
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND]
    assert _state(hass) == AlarmControlPanelState.ARMED_AWAY
    await hass.async_block_till_done()
    assert events[-1].data["step"] == "disarm_first"
    assert events[-1].data["requested"] == "partset"


async def test_switch_fails_at_arming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If arming fails after disarming, the entity shows the real state."""
    await setup(code=CODE)
    await _call(hass, "alarm_arm_away")
    # the partition isn't set up for internal arming
    fake_panel.partitions[1].internal_arming = False
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_home")
    assert err.value.translation_key == "arm_failed_refused"
    assert _state(hass) == AlarmControlPanelState.DISARMED


async def test_rejected_credentials(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 during a command has its own message; nothing is verified."""
    await setup(code=CODE)
    fake_panel.password = "changed"
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "auth_failed"
    # the fresh read got the 401; nothing else was sent
    assert fake_panel.stats.requests == [*ROUND, ("GET", "/system/partitions/")]
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler("secvest")
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_installer_lock_at_the_read(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The lock at the read before the command is reported as the lock."""
    entry = await setup(code=CODE)
    lock = (FIXTURES / "GET_system.403.json").read_bytes()
    fake_panel.inject(
        Injection(
            "GET",
            "/system/partitions/",
            "status",
            status=403,
            body=lock,
            content_type="application/json",
        )
    )
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "installer_locked"
    assert coordinator_of(entry).installer_locked


async def test_installer_logs_in_during_the_command(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The lock at the verification is reported as the lock."""
    entry = await setup(code=CODE)
    lock = (FIXTURES / "GET_system.403.json").read_bytes()
    # /alarms/ is first read in the verification, after the command
    fake_panel.inject(
        Injection(
            "GET",
            "/alarms/",
            "status",
            status=403,
            body=lock,
            content_type="application/json",
        )
    )
    with pytest.raises(CommandError) as err:
        await _call(hass, "alarm_arm_away")
    assert err.value.translation_key == "installer_locked"
    assert coordinator_of(entry).installer_locked
    assert ("PUT", "/system/partitions-1/") in fake_panel.stats.requests
