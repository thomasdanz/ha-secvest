"""Tests for the codes for arming and disarming (#116)."""

from collections.abc import Callable, Mapping
import hashlib
import json
import threading
from typing import Any

from homeassistant.components.alarm_control_panel.const import (
    AlarmControlPanelState,
)
from homeassistant.const import CONF_CODE, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, flush_store

from custom_components.secvest.codes import (
    CONF_HASH,
    CONF_KDF,
    CONF_SALT,
    KDF,
    async_find,
    hash_code,
)
from custom_components.secvest.const import CONF_AUTH_FAILED, CONF_CODES

from .common import ROUND, Setup, arming_failed_events, call_panel, get_state
from .fake_panel import FakePanel


async def _codes_step(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> Any:
    """Open the options, choose the codes, then one of their steps."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "codes"}
    )
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def _add(
    hass: HomeAssistant, entry: MockConfigEntry, data: dict[str, Any]
) -> Any:
    result = await _codes_step(hass, entry, "add_code")
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    await hass.async_block_till_done()
    return result


def _stored(entry: MockConfigEntry) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = entry.options[CONF_CODES]
    return stored


async def test_right_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """With the right code the command is sent; the user is shown."""
    await setup(code="4711")
    await call_panel(hass, "alarm_arm_away", code="4711")
    assert get_state(hass).state == AlarmControlPanelState.ARMED_AWAY
    assert get_state(hass).attributes["changed_by"] == "Tester"
    await call_panel(hass, "alarm_disarm", code="4711")
    assert get_state(hass).state == AlarmControlPanelState.DISARMED


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
        await call_panel(hass, "alarm_disarm", code=code)
    assert err.value.translation_key == "invalid_code"
    assert fake_panel.stats.requests == ROUND
    assert "Wrong code" in caplog.text
    # the entered code is never logged
    assert not code or code not in caplog.text


async def test_code_required_for_arming(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without a code arming isn't even tried.

    Home Assistant refuses it before the entity sees it, so this is the one
    failed arming without an arming_failed event (#143).
    """
    await setup(code="4711")
    state = get_state(hass)
    assert state.attributes["code_arm_required"] is True
    assert state.attributes["code_format"] == "number"
    events = arming_failed_events(hass)
    with pytest.raises(ServiceValidationError) as err:
        await call_panel(hass, "alarm_arm_home", code=None)
    assert err.value.translation_key == "code_arm_required"
    await hass.async_block_till_done()
    assert events == []
    assert fake_panel.stats.requests == ROUND


async def test_no_codes_configured(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without any code, none is asked for; a code passed anyway is ignored."""
    await setup()
    state = get_state(hass)
    assert state.attributes["code_arm_required"] is False
    assert state.attributes["code_format"] is None
    await call_panel(hass, "alarm_arm_away", code=None)
    assert get_state(hass).state == AlarmControlPanelState.ARMED_AWAY
    assert get_state(hass).attributes["changed_by"] is None
    # e.g. the code HomeKit Bridge sends
    await call_panel(hass, "alarm_disarm", code="1234")
    assert get_state(hass).state == AlarmControlPanelState.DISARMED
    assert get_state(hass).attributes["changed_by"] is None


async def test_removing_the_last_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without its last code the panel asks for none any more."""
    entry = await setup(code="4711")
    result = await _codes_step(hass, entry, "remove_code")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NAME: "Tester"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _stored(entry) == []
    assert get_state(hass).attributes["code_arm_required"] is False
    await call_panel(hass, "alarm_arm_home", code=None)
    assert get_state(hass).state == AlarmControlPanelState.ARMED_HOME


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
    coordinator = entry.runtime_data
    assert get_state(hass).attributes["code_arm_required"] is False
    result = await _add(hass, entry, {CONF_NAME: " Anna ", CONF_CODE: "2468"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (stored,) = _stored(entry)
    assert stored[CONF_NAME] == "Anna"
    _assert_hashed(stored, "2468")
    # stored in the options, no subentry, and the entry isn't reloaded
    assert entry.subentries == {}
    assert entry.runtime_data is coordinator
    user = await async_find(hass, entry, "2468")
    assert user is not None
    assert user.name == "Anna"
    # the first code makes codes required
    assert get_state(hass).attributes["code_arm_required"] is True
    with pytest.raises(ServiceValidationError):
        await call_panel(hass, "alarm_arm_away", code="1234")
    await call_panel(hass, "alarm_arm_away", code="2468")
    assert get_state(hass).attributes["changed_by"] == "Anna"


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

    async def change(name: str, data: dict[str, str]) -> Any:
        result = await _codes_step(hass, entry, "change_code")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_NAME: name}
        )
        assert result["step_id"] == "edit_code"
        schema = result["data_schema"]
        assert schema is not None
        suggested = {
            str(k): (k.description or {}).get("suggested_value") for k in schema.schema
        }
        # the code is never shown
        assert suggested == {CONF_NAME: name, CONF_CODE: None}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], data
        )
        await hass.async_block_till_done()
        return result

    result = await change("Tester", {CONF_NAME: "Renamed"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    user = await async_find(hass, entry, "4711")
    assert user is not None
    assert user.name == "Renamed"
    await change("Renamed", {CONF_NAME: "Renamed", CONF_CODE: "1357"})
    assert await async_find(hass, entry, "4711") is None
    assert await async_find(hass, entry, "1357") is not None
    (stored,) = _stored(entry)
    _assert_hashed(stored, "1357")


async def test_codes_menu(hass: HomeAssistant, setup: Setup) -> None:
    """Changing and removing are offered once there is a code."""
    entry = await setup()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["settings", "codes", "names"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "codes"}
    )
    assert result["menu_options"] == ["add_code"]
    await _add(hass, entry, {CONF_NAME: "Anna", CONF_CODE: "2468"})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "codes"}
    )
    assert result["menu_options"] == ["add_code", "change_code", "remove_code"]


async def test_codes_while_not_loaded(hass: HomeAssistant, setup: Setup) -> None:
    """Codes can be managed while the entry isn't loaded, e.g. after a 401."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    result = await _add(hass, entry, {CONF_NAME: "Anna", CONF_CODE: "2468"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [c[CONF_NAME] for c in _stored(entry)] == ["Anna"]


def _assert_hashed(data: Mapping[str, Any], code: str) -> None:
    """Only the name, a salt, the PBKDF2 hash and its parameters, never the code.

    Compared field by field: a substring check could match the hex of a
    random salt by chance.
    """
    assert set(data) == {CONF_NAME, CONF_SALT, CONF_HASH, CONF_KDF}
    # the parameters are stored, so they can change later (#139)
    assert data[CONF_KDF] == KDF
    assert code not in data.values()
    assert len(data[CONF_SALT]) == 32
    assert data[CONF_HASH] == hash_code(code, data[CONF_SALT])[CONF_HASH]


async def test_salted_and_never_stored(
    hass: HomeAssistant, setup: Setup, hass_storage: dict[str, Any]
) -> None:
    """Each code gets its own salt; the stored entry never contains a code."""
    # the same code hashes differently each time
    assert hash_code("2468")[CONF_HASH] != hash_code("2468")[CONF_HASH]
    entry = await setup()
    for name, code in (("Anna", "2468"), ("Ben", "1357")):
        await _add(hass, entry, {CONF_NAME: name, CONF_CODE: code})
    first, second = _stored(entry)
    assert first[CONF_SALT] != second[CONF_SALT]
    # what Home Assistant writes to .storage/core.config_entries
    await flush_store(hass.config_entries._store)
    stored = json.dumps(hass_storage["core.config_entries"])
    assert CONF_HASH in stored
    assert '"2468"' not in stored
    assert '"1357"' not in stored


async def test_hashed_off_the_event_loop(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PBKDF2 never runs in the event loop: arming and the code flow (#139)."""
    entry = await setup(code="4711")
    threads: list[threading.Thread] = []
    pbkdf2 = hashlib.pbkdf2_hmac

    def recording(*args: Any) -> bytes:
        threads.append(threading.current_thread())
        return pbkdf2(*args)

    # codes.py looks it up at each call
    monkeypatch.setattr(hashlib, "pbkdf2_hmac", recording)
    await call_panel(hass, "alarm_arm_away", code="4711")
    await _add(hass, entry, {CONF_NAME: "Anna", CONF_CODE: "2468"})
    assert threads
    assert threading.main_thread() not in threads


@pytest.mark.parametrize(
    "stored",
    [
        # stored before 0.3: no parameters, today's are meant
        lambda code: {k: v for k, v in hash_code(code).items() if k != CONF_KDF},
        # other parameters are read back and used
        lambda code: {
            CONF_SALT: "00" * 16,
            CONF_HASH: hashlib.pbkdf2_hmac(
                "sha256", code.encode(), bytes(16), 1000
            ).hex(),
            CONF_KDF: "pbkdf2-sha256-1000",
        },
    ],
    ids=["without_parameters", "other_iterations"],
)
async def test_stored_parameters(
    hass: HomeAssistant,
    setup: Setup,
    stored: Callable[[str], dict[str, Any]],
) -> None:
    """A code matches with the parameters it was stored with."""
    entry = await setup(code="4711")
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_CODES: [{CONF_NAME: "Tester", **stored("1357")}],
        },
    )
    await hass.async_block_till_done()
    user = await async_find(hass, entry, "1357")
    assert user is not None
    assert await async_find(hass, entry, "4711") is None


async def test_unknown_parameters_never_match(
    hass: HomeAssistant, setup: Setup
) -> None:
    """A code stored with unknown parameters isn't guessed at."""
    entry = await setup(code="4711")
    (stored,) = _stored(entry)
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_CODES: [{**stored, CONF_KDF: "argon2id-x"}]},
    )
    await hass.async_block_till_done()
    assert await async_find(hass, entry, "4711") is None
