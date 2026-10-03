"""Tests for arming and disarming with verified success (#16, #17)."""

from pathlib import Path

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.core import Context, HomeAssistant, callback
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.secvest.api.errors import ArmingBlockedError
from custom_components.secvest.api.models import PanelEvent, PartitionState
from custom_components.secvest.commands import (
    CommandError,
    Failure,
    explain,
)
from custom_components.secvest.const import CONF_PARTITIONS
from custom_components.secvest.coordinator import CommandOutcome

from .common import (
    CODE,
    PANEL,
    ROUND,
    Setup,
    arming_failed_events,
    call_panel,
    coordinator_of,
    get_state,
    state_of,
)
from .fake_panel import FakePanel, Injection

FIXTURES = Path(__file__).parent / "fixtures"


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
    await call_panel(hass, service)
    assert state_of(hass) == expected
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
    await call_panel(hass, "alarm_arm_away")
    await call_panel(hass, "alarm_disarm")
    assert state_of(hass) == AlarmControlPanelState.DISARMED
    assert coordinator_of(entry).data.partitions[1].state == PartitionState.UNSET


async def test_blocked(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """409 names the blocking zones (certain); the event says the same."""
    await setup(code=CODE)
    events = arming_failed_events(hass)
    fake_panel.open_zone("209")
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_away")
    assert err.value.translation_key == "arm_failed_blocked"
    assert err.value.translation_placeholders == {
        "partition": "Teilber. 1",
        "items": "Room 6 L",
    }
    assert str(err.value) == "Partition Teilber. 1 was not armed: blocked by Room 6 L"
    # the entity stays in its real state
    assert state_of(hass) == AlarmControlPanelState.DISARMED
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
            "zone_names": ["Room 6 L"],
            "faults": [],
            "user": "Tester",
        }
    ]


@pytest.mark.parametrize("as_user", [False, True])
async def test_event_keeps_the_context(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    hass_admin_user: MockUser,
    as_user: bool,
) -> None:
    """The event carries the action's context, so automations see its origin.

    HomeKit Bridge calls without a user; the frontend with the user's id.
    """
    await setup(code=CODE)
    events = arming_failed_events(hass)
    fake_panel.open_zone("209")
    user_id = hass_admin_user.id if as_user else None
    context = Context(user_id=user_id)
    with pytest.raises(CommandError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_arm_away",
            {"entity_id": PANEL, "code": CODE},
            blocking=True,
            context=context,
        )
    await hass.async_block_till_done()
    assert [event.context.id for event in events] == [context.id]
    assert events[0].context.user_id == user_id


async def test_refused_without_reason(hass: HomeAssistant, setup: Setup) -> None:
    """409 with an empty list: a partition without zones."""
    await setup(code=CODE, **{CONF_PARTITIONS: [1, 2]})
    with pytest.raises(CommandError) as err:
        await call_panel(
            hass, "alarm_arm_away", "alarm_control_panel.alarmanlage_teilber_2"
        )
    assert err.value.translation_key == "arm_failed_refused"


async def test_no_permission(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An empty 403 means no permission for the partition (certain)."""
    await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    fake_panel.rights = {2}
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_disarm")
    assert err.value.translation_key == "disarm_failed_no_permission"
    assert str(err.value) == (
        "Partition Teilber. 1 was not disarmed: no permission for this partition"
    )
    assert state_of(hass) == AlarmControlPanelState.ARMED_AWAY
    fake_panel.rights = None
    await call_panel(hass, "alarm_disarm")
    fake_panel.rights = {2}
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_away")
    assert err.value.translation_key == "arm_failed_no_permission"


async def test_silently_ignored(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """200 without effect: the likely reason comes from the fresh state."""
    await setup(code=CODE)
    events = arming_failed_events(hass)
    # an open entry door makes the reference panel ignore "set"
    fake_panel.open_zone("219")
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_away")
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
        await call_panel(hass, "alarm_arm_away")
    assert err.value.translation_key == "installer_locked"


@pytest.mark.parametrize(
    "service", ["alarm_arm_away", "alarm_arm_home", "alarm_disarm"]
)
async def test_known_installer_lock_sends_nothing(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, service: str
) -> None:
    """With the lock seen by the last round, nothing is sent, not even a read."""
    entry = await setup(code=CODE)
    fake_panel.installer_locked = True
    await coordinator_of(entry).async_refresh()
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await call_panel(hass, service)
    assert err.value.translation_key == "installer_locked"
    assert len(fake_panel.stats.requests) == sent
    assert state_of(hass) == AlarmControlPanelState.DISARMED


async def test_unreachable(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If the read before the command fails, nothing is sent (#128)."""
    await setup(code=CODE)
    fake_panel.inject(Injection("GET", "/system/partitions/", "drop_before", times=2))
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_away")
    assert err.value.translation_key == "unreachable"
    assert ("PUT", "/system/partitions-1/") not in fake_panel.stats.requests


async def test_not_verified(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If the result can't be read back, the message says so."""
    await setup(code=CODE)
    # /faults/ is first read in the verification, after the command
    fake_panel.inject(Injection("GET", "/faults/", "drop_before", times=2))
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_away")
    assert err.value.translation_key == "not_verified"
    assert ("PUT", "/system/partitions-1/") in fake_panel.stats.requests


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


async def test_explain_open_zones_before_faults(
    fake_panel: FakePanel, setup: Setup
) -> None:
    """With open zones and blocking faults, the open zones are the reason."""
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
    fake_panel.open_zone("209")
    await coordinator_of(entry).async_refresh()
    outcome = CommandOutcome(False, None, coordinator_of(entry).data, False)
    assert coordinator_of(entry).data.blocking_problems(1)
    assert explain(outcome, 1, PartitionState.SET) == Failure(
        "likely_open_zones", ("209",)
    )


PUT = ("PUT", "/system/partitions-1/")
# the fresh read before deciding what to send
READ = ("GET", "/system/partitions/")


async def test_switch_modes(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Away → home disarms first; the entity doesn't show the step between."""
    await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    shown: list[str] = []
    # a callback, so the states are collected in order in the event loop
    hass.bus.async_listen(
        "state_changed",
        callback(
            lambda event: (
                shown.append(event.data["new_state"].state)
                if event.data["entity_id"] == PANEL
                else None
            )
        ),
    )
    sent = len(fake_panel.stats.requests)
    await call_panel(hass, "alarm_arm_home")
    await hass.async_block_till_done()
    assert state_of(hass) == AlarmControlPanelState.ARMED_HOME
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND, PUT, *ROUND]
    assert AlarmControlPanelState.DISARMED not in shown


async def test_same_mode_sends_nothing(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming into the current mode only reads the state (#142)."""
    await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    sent = len(fake_panel.stats.requests)
    events = arming_failed_events(hass)
    await call_panel(hass, "alarm_arm_away")
    assert fake_panel.stats.requests[sent:] == [READ]
    assert state_of(hass) == AlarmControlPanelState.ARMED_AWAY
    await hass.async_block_till_done()
    assert events == []


async def test_disarming_a_disarmed_partition_sends_nothing(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Disarming a disarmed partition only reads the state (#142)."""
    await setup(code=CODE)
    await call_panel(hass, "alarm_disarm")
    assert fake_panel.stats.requests[len(ROUND) :] == [READ]
    assert state_of(hass) == AlarmControlPanelState.DISARMED


async def test_target_reached_meanwhile(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Armed at the keypad since the last round: shown, nobody changed it."""
    await setup(code=CODE)
    fake_panel.partitions[1].state = "set"
    assert state_of(hass) == AlarmControlPanelState.DISARMED
    await call_panel(hass, "alarm_arm_away")
    assert fake_panel.stats.requests[len(ROUND) :] == [READ]
    assert state_of(hass) == AlarmControlPanelState.ARMED_AWAY
    assert get_state(hass).attributes["changed_by"] is None


async def test_changed_by_cleared_by_a_change_elsewhere(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """changed_by names the user only while their command is the last change."""
    entry = await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    assert get_state(hass).attributes["changed_by"] == "Tester"
    # the verification and later rounds without a change keep it
    await coordinator_of(entry).async_refresh()
    assert get_state(hass).attributes["changed_by"] == "Tester"
    # disarmed at the keypad
    fake_panel.partitions[1].state = "unset"
    await coordinator_of(entry).async_refresh()
    assert state_of(hass) == AlarmControlPanelState.DISARMED
    assert get_state(hass).attributes["changed_by"] is None


async def test_changed_by_kept_for_repeated_commands(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming twice from Home Assistant keeps the user (#144)."""
    await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    await call_panel(hass, "alarm_arm_away")
    assert get_state(hass).attributes["changed_by"] == "Tester"


async def test_changed_by_cleared_when_reached_elsewhere(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Disarmed and armed again at the keypad between two rounds (#144)."""
    entry = await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    fake_panel.partitions[1].state = "unset"
    await coordinator_of(entry).async_refresh()
    fake_panel.partitions[1].state = "set"
    # nothing is sent, but the state shown changed meanwhile
    await call_panel(hass, "alarm_arm_away")
    assert get_state(hass).attributes["changed_by"] is None


async def test_switch_fails_at_disarming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed first step stops the sequence and is named."""
    await setup(code=CODE)
    events = arming_failed_events(hass)
    await call_panel(hass, "alarm_arm_away")
    fake_panel.rights = {2}
    sent = len(fake_panel.stats.requests)
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_home")
    assert err.value.translation_key == "switch_failed_no_permission"
    assert "disarming first failed" in str(err.value)
    assert fake_panel.stats.requests[sent:] == [READ, PUT, *ROUND]
    assert state_of(hass) == AlarmControlPanelState.ARMED_AWAY
    await hass.async_block_till_done()
    assert events[-1].data["step"] == "disarm_first"
    assert events[-1].data["requested"] == "partset"


async def test_switch_fails_at_arming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If arming fails after disarming, the entity shows the real state."""
    await setup(code=CODE)
    await call_panel(hass, "alarm_arm_away")
    # the partition isn't set up for internal arming
    fake_panel.partitions[1].internal_arming = False
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_home")
    assert err.value.translation_key == "arm_failed_refused"
    assert state_of(hass) == AlarmControlPanelState.DISARMED


async def test_rejected_credentials(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 during a command has its own message; nothing is verified."""
    await setup(code=CODE)
    fake_panel.password = "changed"
    with pytest.raises(CommandError) as err:
        await call_panel(hass, "alarm_arm_away")
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
        await call_panel(hass, "alarm_arm_away")
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
        await call_panel(hass, "alarm_arm_away")
    assert err.value.translation_key == "installer_locked"
    assert coordinator_of(entry).installer_locked
    assert ("PUT", "/system/partitions-1/") in fake_panel.stats.requests
