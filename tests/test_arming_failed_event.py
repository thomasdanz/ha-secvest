"""The arming failed event for failures without a verified state (#126).

HomeKit Bridge shows no message, so the event is the only way to notify
such a user; it's fired once for every failed arm or disarm command.
"""

from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest

from .common import CODE, PANEL, Setup, arming_failed_events, call_panel, coordinator_of
from .fake_panel import FakePanel, Injection

LOCK = (Path(__file__).parent / "fixtures" / "GET_system.403.json").read_bytes()


async def _failed(
    hass: HomeAssistant, service: str, code: str | None = CODE
) -> dict[str, Any]:
    """Call the service, expect it to fail, return the one event's data."""
    events = arming_failed_events(hass)
    with pytest.raises(HomeAssistantError):
        await call_panel(hass, service, code=code)
    await hass.async_block_till_done()
    assert len(events) == 1
    return dict(events[0].data)


def _lock_at(path: str) -> Injection:
    return Injection(
        "GET", path, "status", status=403, body=LOCK, content_type="application/json"
    )


async def test_installer_lock_known(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Refused before sending anything: the event says why."""
    entry = await setup(code=CODE)
    fake_panel.installer_locked = True
    await coordinator_of(entry).async_refresh()
    data = await _failed(hass, "alarm_arm_away")
    assert data["reason"] == "installer_locked"
    assert data["requested"] == "set"
    assert data["step"] == "command"
    assert data["user"] == "Tester"
    assert data["zones"] == data["zone_names"] == data["faults"] == []


async def test_installer_lock_at_the_read(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A lock found at the read before the command."""
    await setup(code=CODE)
    fake_panel.inject(_lock_at("/system/partitions/"))
    data = await _failed(hass, "alarm_disarm")
    assert data["reason"] == "installer_locked"
    assert data["requested"] == "unset"


async def test_not_verified(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The state is unclear: the event is the hint to check the panel."""
    await setup(code=CODE)
    # /faults/ is first read in the verification, after the command
    fake_panel.inject(Injection("GET", "/faults/", "drop_before", times=2))
    data = await _failed(hass, "alarm_arm_home")
    assert data["reason"] == "not_verified"


async def test_unreachable(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unreachable before sending: certainly nothing changed."""
    await setup(code=CODE)
    fake_panel.inject(Injection("GET", "/system/partitions/", "drop_before", times=2))
    data = await _failed(hass, "alarm_arm_home")
    assert data["reason"] == "unreachable"


async def test_failed_step_is_named(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Switching modes breaks off at disarming: the step says so."""
    await setup(code=CODE)
    await hass.services.async_call(
        "alarm_control_panel",
        "alarm_arm_away",
        {"entity_id": PANEL, "code": CODE},
        blocking=True,
    )
    # /alarms/ is first read in the verification of the first step
    fake_panel.inject(_lock_at("/alarms/"))
    data = await _failed(hass, "alarm_arm_home")
    assert data["reason"] == "installer_locked"
    assert data["requested"] == "partset"
    assert data["step"] == "disarm_first"


async def test_rejected_credentials(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 during the command."""
    await setup(code=CODE)
    fake_panel.password = "changed"
    data = await _failed(hass, "alarm_arm_away")
    assert data["reason"] == "auth_failed"


async def test_arming_during_an_alarm(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming during an alarm isn't sent."""
    entry = await setup(code=CODE)
    fake_panel.partitions[1].state = "set"
    fake_panel.trigger_alarm(1, "209")
    await coordinator_of(entry).async_refresh()
    data = await _failed(hass, "alarm_arm_home")
    assert data["reason"] == "arm_during_alarm"


async def test_wrong_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """No user, and nothing about the entered code."""
    await setup(code=CODE)
    data = await _failed(hass, "alarm_disarm", "1234")
    assert data["reason"] == "invalid_code"
    assert data["requested"] == "unset"
    assert data["user"] is None
    assert "1234" not in str(data)
