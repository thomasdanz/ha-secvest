"""Tests for the reauthentication after a 401 (#41, #6)."""

from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.const import CONF_AUTH_FAILED, CONF_USER_CODE, DOMAIN

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel

LOCK_SENSOR = "binary_sensor.alarmanlage_installer_lock"


def _reauth_flows(hass: HomeAssistant) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_401_during_polling(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 stops polling, remembers it and asks for new credentials."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    fake_panel.password = "changed"
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert entry.data[CONF_AUTH_FAILED] is True
    assert len(_reauth_flows(hass)) == 1
    assert hass.states.get(LOCK_SENSOR).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    await coordinator.async_refresh()
    assert fake_panel.stats.requests == [*ROUND, ("GET", "/system/partitions/")]


async def test_401_at_setup(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A 401 at setup is remembered as well."""
    entry = await setup(password="wrong")
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.data[CONF_AUTH_FAILED] is True
    assert len(_reauth_flows(hass)) == 1


async def test_nothing_sent_after_a_restart(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """With the flag set, setup sends nothing and asks for credentials."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert fake_panel.stats.requests == []
    assert len(_reauth_flows(hass)) == 1


async def _reauth(hass: HomeAssistant, entry: MockConfigEntry, password: str) -> Any:
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {"name": "Alarmanlage"}
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USER_CODE: entry.data[CONF_USER_CODE], CONF_PASSWORD: password},
    )


async def test_reauthenticate(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """New credentials are checked once, stored, and the entry reloads."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    fake_panel.password = "new"
    result = await _reauth(hass, entry, "still wrong")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert fake_panel.stats.requests == [("GET", "/system/")]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USER_CODE: entry.data[CONF_USER_CODE], CONF_PASSWORD: "new"},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.data[CONF_AUTH_FAILED] is False
    assert entry.state is ConfigEntryState.LOADED
    # one check per attempt, then the first round of the reloaded entry
    assert fake_panel.stats.requests == [
        ("GET", "/system/"),
        ("GET", "/system/"),
        *ROUND,
    ]


async def test_reauth_suggests_the_user_code(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The form is prefilled with the stored user code, not the password."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    result = await entry.start_reauth_flow(hass)
    schema = result["data_schema"].schema
    suggested = {
        str(key): (key.description or {}).get("suggested_value") for key in schema
    }
    assert suggested == {CONF_USER_CODE: fake_panel.user_code, CONF_PASSWORD: None}
