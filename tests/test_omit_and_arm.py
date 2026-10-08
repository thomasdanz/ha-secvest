"""Omitting the open zones that block arming once, then arming (#118)."""

import json
from typing import Any

from homeassistant.components.alarm_control_panel.const import AlarmControlPanelState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.secvest import commands
from custom_components.secvest.commands import CommandError
from custom_components.secvest.const import DOMAIN

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

ARM = ("PUT", "/system/partitions-1/")
# the fresh read before deciding what to omit
READS = [
    ("GET", "/system/partitions/"),
    ("GET", "/system/partitions-1/zones/"),
    ("GET", "/faults/"),
]


def _zone(zone_id: str) -> tuple[str, str]:
    return ("PUT", f"/system/partitions-1/zones-{zone_id}/")


def _puts(panel: FakePanel) -> list[tuple[str, str]]:
    return [request for request in panel.stats.requests if request[0] == "PUT"]


async def _omit_and_arm(
    hass: HomeAssistant, mode: str = "away", code: str | None = CODE
) -> None:
    data: dict[str, Any] = {"entity_id": PANEL, "mode": mode}
    if code is not None:
        data["code"] = code
    await hass.services.async_call(DOMAIN, "omit_and_arm", data, blocking=True)


async def test_omits_open_zones_and_arms(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Each open zone is omitted, verified, then the partition is armed."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.zones["210"].state = "open"
    await _omit_and_arm(hass)
    assert fake_panel.partitions[1].state == "set"
    assert fake_panel.zones["209"].omitted
    assert fake_panel.zones["210"].omitted
    assert state_of(hass) == AlarmControlPanelState.ARMED_AWAY
    assert get_state(hass).attributes["changed_by"] == "Tester"
    assert fake_panel.stats.requests[len(ROUND) : len(ROUND) + 4] == [
        *READS,
        _zone("209"),
    ]
    assert _puts(fake_panel) == [_zone("209"), _zone("210"), ARM]


async def test_home(hass: HomeAssistant, fake_panel: FakePanel, setup: Setup) -> None:
    """Home arms internally."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    await _omit_and_arm(hass, mode="home")
    assert fake_panel.partitions[1].state == "partset"
    assert state_of(hass) == AlarmControlPanelState.ARMED_HOME


async def test_home_leaves_zones_not_monitored_internally(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """At partset, a zone without "inner" is neither omitted nor refused.

    The panel decides; the fake panel, like the reference panel, has no such
    zone and refuses as for any open zone.
    """
    await setup(code=CODE)
    fake_panel.zones["211"].inner = False
    fake_panel.zones["211"].state = "open"
    with pytest.raises(CommandError) as err:
        await _omit_and_arm(hass, mode="home")
    assert err.value.translation_key == "arm_failed_blocked"
    assert _puts(fake_panel) == [ARM]


async def test_away_counts_every_zone(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """At set, a zone without "inner" blocks like any other."""
    await setup(code=CODE)
    fake_panel.zones["211"].inner = False
    fake_panel.zones["211"].state = "open"
    with pytest.raises(CommandError) as err:
        await _omit_and_arm(hass)
    assert err.value.translation_key == "arm_failed_not_omittable"
    assert _puts(fake_panel) == []


async def test_nothing_open(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without open zones, it just arms."""
    await setup()
    await _omit_and_arm(hass, code=None)
    assert fake_panel.partitions[1].state == "set"
    assert _puts(fake_panel) == [ARM]


async def test_already_armed(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An armed partition is left to the regular command: nothing is sent."""
    entry = await setup()
    fake_panel.partitions[1].state = "set"
    fake_panel.zones["209"].state = "open"
    await coordinator_of(entry).async_refresh()
    await _omit_and_arm(hass, code=None)
    assert _puts(fake_panel) == []
    assert not fake_panel.zones["209"].omitted


@pytest.mark.parametrize("zone_id", ["211", "219"])
async def test_zone_that_cant_be_omitted(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup, zone_id: str
) -> None:
    """If an open zone can't be omitted, nothing is omitted or sent."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.zones[zone_id].state = "open"
    events = arming_failed_events(hass)
    with pytest.raises(CommandError) as err:
        await _omit_and_arm(hass)
    assert err.value.translation_key == "arm_failed_not_omittable"
    assert _puts(fake_panel) == []
    await hass.async_block_till_done()
    (event,) = events
    assert event.data["reason"] == "not_omittable"
    assert event.data["zones"] == [zone_id]
    assert event.data["can_omit_and_arm"] is False
    assert event.data["omit_and_arm"] is True


async def test_other_fault_blocks(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A fault that prevents arming is named, and nothing is omitted."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.static_faults.append(
        {
            "type": "1170",
            "id": "1104",
            "ui-string": "REP01 Batt schwach",
            "affects-partition": ["1"],
            "prevents-set": True,
        }
    )
    events = arming_failed_events(hass)
    with pytest.raises(CommandError):
        await _omit_and_arm(hass)
    assert _puts(fake_panel) == []
    await hass.async_block_till_done()
    assert events[0].data["faults"] == ["REP01 Batt schwach"]


async def test_arming_fails_after_omitting(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If arming fails anyway, the zones are included again, verified."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.inject(Injection(*ARM, "status", status=500))
    events = arming_failed_events(hass)
    with pytest.raises(CommandError) as err:
        await _omit_and_arm(hass)
    assert err.value.translation_key == "arm_failed_error"
    assert not fake_panel.zones["209"].omitted
    assert fake_panel.partitions[1].state == "unset"
    assert _puts(fake_panel) == [_zone("209"), ARM, _zone("209")]
    await hass.async_block_till_done()
    assert [event.data["reason"] for event in events] == ["error"]
    assert events[0].data["omit_and_arm"] is True


async def test_zone_stays_omitted(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zone that can't be included again gets its own event."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.inject(Injection(*ARM, "status", status=500))
    omit = commands.async_set_omitted

    async def include_fails(coordinator: Any, zone_id: str, omitted: bool) -> None:
        if not omitted:
            raise CommandError("include failed")
        await omit(coordinator, zone_id, omitted)

    monkeypatch.setattr(commands, "async_set_omitted", include_fails)
    events = arming_failed_events(hass)
    with pytest.raises(CommandError):
        await _omit_and_arm(hass)
    assert fake_panel.zones["209"].omitted
    await hass.async_block_till_done()
    assert [event.data["reason"] for event in events] == ["error", "still_omitted"]
    assert events[1].data["zones"] == ["209"]


def _blocked_by(*faults: dict[str, Any]) -> Injection:
    """Answer arming with 409 and these blocking faults."""
    return Injection(
        *ARM,
        "status",
        status=409,
        body=json.dumps(list(faults)).encode(),
        content_type="application/json",
    )


def _zone_fault(zone_id: str) -> dict[str, Any]:
    return {
        "type": "5000",
        "id": str(1305 + int(zone_id)),
        "ui-string": f"Z{zone_id} A",
        "affects-partition": ["1"],
        "affects-zone": zone_id,
        "prevents-set": True,
    }


async def test_not_offered_again(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Blocked by another zone after omitting, it isn't offered once more."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.inject(_blocked_by(_zone_fault("210")))
    events = arming_failed_events(hass)
    with pytest.raises(CommandError):
        await _omit_and_arm(hass)
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "blocked"
    assert events[0].data["zones"] == ["210"]
    assert events[0].data["can_omit_and_arm"] is False


async def test_arming_unreachable_after_omitting(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure without a verified state fires the event, then includes again."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"

    async def unreachable(coordinator: Any) -> None:
        raise CommandError(translation_domain=DOMAIN, translation_key="unreachable")

    monkeypatch.setattr(commands, "_read_current", unreachable)
    events = arming_failed_events(hass)
    with pytest.raises(CommandError):
        await _omit_and_arm(hass)
    await hass.async_block_till_done()
    assert [event.data["reason"] for event in events] == ["unreachable"]
    assert not fake_panel.zones["209"].omitted


async def test_omitting_fails(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If a zone can't be omitted, those omitted before are included again."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.zones["210"].state = "open"
    fake_panel.inject(Injection(*_zone("210"), "status", status=500))
    events = arming_failed_events(hass)
    with pytest.raises(CommandError) as err:
        await _omit_and_arm(hass)
    assert err.value.translation_key == "arm_failed_omit_failed"
    placeholders = err.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["items"] == fake_panel.zones["210"].name
    assert not fake_panel.zones["209"].omitted
    assert not fake_panel.zones["210"].omitted
    assert ARM not in _puts(fake_panel)
    await hass.async_block_till_done()
    assert [event.data["reason"] for event in events] == ["omit_failed"]


async def test_wrong_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The code is checked like for arming; nothing is sent without it."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    events = arming_failed_events(hass)
    for code in ("0000", None):
        with pytest.raises(ServiceValidationError):
            await _omit_and_arm(hass, code=code)
    assert fake_panel.stats.requests == ROUND
    await hass.async_block_till_done()
    assert [event.data["reason"] for event in events] == ["invalid_code"] * 2


async def test_unreachable(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed read before deciding sends nothing."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    fake_panel.inject(Injection("GET", "/faults/", "drop_before", times=2))
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await _omit_and_arm(hass)
    assert _puts(fake_panel) == []
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "unreachable"


async def test_rejected_credentials_at_the_read(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 at the read stops everything, as in a round."""
    entry = await setup(code=CODE)
    fake_panel.inject(Injection("GET", "/faults/", "status", status=401))
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await _omit_and_arm(hass)
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "auth_failed"
    assert entry.data["auth_failed"] is True
    fake_panel.violations.clear()


async def test_installer_lock_known(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A known lock refuses before anything is sent."""
    entry = await setup(code=CODE)
    fake_panel.installer_locked = True
    await coordinator_of(entry).async_refresh()
    sent = list(fake_panel.stats.requests)
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await _omit_and_arm(hass)
    assert fake_panel.stats.requests == sent
    await hass.async_block_till_done()
    assert events[0].data["reason"] == "installer_locked"


# the event tells an automation whether to offer it


async def test_event_offers_it_for_omittable_zones(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Blocked by open zones that can be omitted: offered, with the entity."""
    await setup(code=CODE)
    fake_panel.zones["209"].state = "open"
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await call_panel(hass, "alarm_arm_away")
    await hass.async_block_till_done()
    assert events[0].data["can_omit_and_arm"] is True
    assert events[0].data["entity_id"] == PANEL


async def test_event_doesnt_offer_it_with_other_faults(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A fault besides the open zone isn't omitted, so it isn't offered."""
    await setup(code=CODE)
    fake_panel.inject(
        _blocked_by(
            _zone_fault("209"),
            {
                "type": "1170",
                "id": "1104",
                "ui-string": "REP01 Batt schwach",
                "affects-partition": ["1"],
                "prevents-set": True,
            },
        )
    )
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await call_panel(hass, "alarm_arm_away")
    await hass.async_block_till_done()
    assert events[0].data["zones"] == ["209"]
    assert events[0].data["can_omit_and_arm"] is False


async def test_event_doesnt_offer_it_when_switching(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Disarming first, when switching modes, isn't arming."""
    entry = await setup(code=CODE)
    fake_panel.partitions[1].state = "partset"
    await coordinator_of(entry).async_refresh()
    fake_panel.inject(_blocked_by(_zone_fault("209")))
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await call_panel(hass, "alarm_arm_away")
    await hass.async_block_till_done()
    assert events[0].data["step"] == "disarm_first"
    assert events[0].data["can_omit_and_arm"] is False


async def test_event_doesnt_offer_it_otherwise(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An open entry door can't be omitted; disarming is never offered."""
    entry = await setup(code=CODE)
    fake_panel.zones["211"].state = "open"
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await call_panel(hass, "alarm_arm_away")
    fake_panel.zones["211"].state = "closed"
    fake_panel.partitions[1].state = "set"
    fake_panel.zones["209"].state = "open"
    await coordinator_of(entry).async_refresh()
    fake_panel.inject(Injection(*ARM, "status", status=500))
    with pytest.raises(HomeAssistantError):
        await call_panel(hass, "alarm_disarm")
    await hass.async_block_till_done()
    assert [event.data["can_omit_and_arm"] for event in events] == [False, False]
