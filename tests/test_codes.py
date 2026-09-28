"""Tests for the codes for arming and disarming (#116)."""

from typing import Any

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_CODE, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.codes import find
from custom_components.secvest.const import SUBENTRY_CODE

from .common import ROUND, Setup
from .fake_panel import FakePanel

PANEL = "alarm_control_panel.alarmanlage_teilber_1"


async def _call(hass: HomeAssistant, service: str, code: str | None) -> None:
    data: dict[str, Any] = {"entity_id": PANEL}
    if code is not None:
        data["code"] = code
    await hass.services.async_call("alarm_control_panel", service, data, blocking=True)


async def _add(
    hass: HomeAssistant, entry: MockConfigEntry, data: dict[str, Any]
) -> Any:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_CODE), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], data
    )
    await hass.async_block_till_done()
    return result


def _state(hass: HomeAssistant) -> Any:
    state = hass.states.get(PANEL)
    assert state is not None
    return state


async def test_right_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """With the right code the command is sent; the user is shown."""
    await setup(code="4711")
    await _call(hass, "alarm_arm_away", "4711")
    assert _state(hass).state == AlarmControlPanelState.ARMED_AWAY
    assert _state(hass).attributes["changed_by"] == "Tester"
    await _call(hass, "alarm_disarm", "4711")
    assert _state(hass).state == AlarmControlPanelState.DISARMED


@pytest.mark.parametrize("code", ["1234", "", "abcd", "47111"])
async def test_wrong_code_sends_nothing(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
    code: str,
) -> None:
    """A wrong code fails before anything is sent."""
    await setup(code="4711")
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "alarm_disarm", code)
    assert err.value.translation_key == "invalid_code"
    assert fake_panel.stats.requests == ROUND
    assert "Wrong code" in caplog.text
    # the entered code is never logged
    assert not code or code not in caplog.text


async def test_code_required_for_arming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without a code arming isn't even tried."""
    await setup(code="4711")
    with pytest.raises(ServiceValidationError):
        await _call(hass, "alarm_arm_home", None)
    assert fake_panel.stats.requests == ROUND


async def test_no_codes_configured(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """With no code configured, arming and disarming aren't possible."""
    await setup()
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, "alarm_arm_away", "4711")
    assert err.value.translation_key == "no_codes"
    assert fake_panel.stats.requests == ROUND


async def test_omitting_needs_no_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Everything but arming and disarming works without a code."""
    await setup()
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.alarmanlage_room_6_l_omit"},
        blocking=True,
    )
    assert fake_panel.zones["209"].omitted


async def test_add_code(hass: HomeAssistant, setup: Setup) -> None:
    """A code is stored only as a salted hash."""
    entry = await setup()
    result = await _add(hass, entry, {CONF_NAME: " Anna ", CONF_CODE: "2468"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (subentry,) = entry.subentries.values()
    assert subentry.title == "Anna"
    assert subentry.data[CONF_NAME] == "Anna"
    assert "2468" not in str(dict(subentry.data))
    user = find(entry, "2468")
    assert user is not None
    assert user.name == "Anna"
    await _call(hass, "alarm_arm_away", "2468")
    assert _state(hass).attributes["changed_by"] == "Anna"


@pytest.mark.parametrize(
    ("data", "errors"),
    [
        ({CONF_NAME: "Anna", CONF_CODE: "123"}, {CONF_CODE: "invalid_code"}),
        ({CONF_NAME: "Anna", CONF_CODE: "12a4"}, {CONF_CODE: "invalid_code"}),
        ({CONF_NAME: "Anna", CONF_CODE: "12345"}, {CONF_CODE: "invalid_code"}),
        ({CONF_NAME: " ", CONF_CODE: "2468"}, {CONF_NAME: "name_required"}),
        ({CONF_NAME: "tester", CONF_CODE: "2468"}, {CONF_NAME: "name_exists"}),
        ({CONF_NAME: "Anna", CONF_CODE: "4711"}, {CONF_CODE: "code_exists"}),
    ],
)
async def test_invalid_code_input(
    hass: HomeAssistant, setup: Setup, data: dict[str, str], errors: dict[str, str]
) -> None:
    """Four digits, a name, and both unique."""
    entry = await setup(code="4711")
    result = await _add(hass, entry, data)
    assert result["errors"] == errors


async def test_change_code(hass: HomeAssistant, setup: Setup) -> None:
    """An empty code keeps the current one; a new one replaces it."""
    entry = await setup(code="4711")
    (subentry_id,) = entry.subentries

    async def reconfigure(data: dict[str, str], shown: str) -> Any:
        result = await hass.config_entries.subentries.async_init(
            (entry.entry_id, SUBENTRY_CODE),
            context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id},
        )
        schema = result["data_schema"]
        assert schema is not None
        suggested = {
            str(k): (k.description or {}).get("suggested_value") for k in schema.schema
        }
        # the code is never shown
        assert suggested == {CONF_NAME: shown, CONF_CODE: None}
        result = await hass.config_entries.subentries.async_configure(
            result["flow_id"], data
        )
        await hass.async_block_till_done()
        return result

    result = await reconfigure({CONF_NAME: "Renamed"}, "Tester")
    assert result["reason"] == "reconfigure_successful"
    user = find(entry, "4711")
    assert user is not None
    assert user.name == "Renamed"
    await reconfigure({CONF_NAME: "Renamed", CONF_CODE: "1357"}, "Renamed")
    assert find(entry, "4711") is None
    assert find(entry, "1357") is not None
