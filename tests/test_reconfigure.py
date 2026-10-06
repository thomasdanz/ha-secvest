"""Tests for changing address, credentials and certificate check (#138)."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest import coordinator as coordinator_module
from custom_components.secvest.config_flow import SecvestConfigFlow
from custom_components.secvest.const import CONF_AUTH_FAILED, CONF_USER_CODE, DOMAIN

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel


@pytest.fixture
def new_panel(
    panel_certificate: tuple[Path, Path], fake_panel: FakePanel
) -> Iterator[FakePanel]:
    """Run a second fake panel, at another address."""
    panel = FakePanel(*panel_certificate)
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()
    panel.assert_no_violations()


def _input(fake_panel: FakePanel, **changes: Any) -> dict[str, Any]:
    return {
        CONF_URL: f"{fake_panel.host}:{fake_panel.port}",
        CONF_USER_CODE: fake_panel.user_code,
        CONF_VERIFY_SSL: False,
        **changes,
    }


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, user_input: dict[str, Any]
) -> Any:
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input
    )
    await hass.async_block_till_done()
    return result


async def test_form_is_prefilled(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Address, user code and certificate check are suggested, never the password."""
    entry = await setup()
    result = await entry.start_reconfigure_flow(hass)
    assert result["description_placeholders"] == {"name": "Alarmanlage"}
    suggested = {
        str(key): (key.description or {}).get("suggested_value")
        for key in result["data_schema"].schema
    }
    assert suggested == {
        CONF_URL: fake_panel.url,
        CONF_USER_CODE: fake_panel.user_code,
        CONF_PASSWORD: None,
        CONF_VERIFY_SSL: False,
    }
    assert fake_panel.stats.requests == ROUND


async def test_change_address_while_loaded(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    new_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new address is checked once, stored, and the entry reloads.

    The password is kept when left empty, entities stay, and the round read
    from the old address isn't reused: it may have been another panel's.
    """
    entry = await setup()
    # long enough that the old round would be taken (quick reload)
    monkeypatch.setattr(coordinator_module, "MIN_SCAN_INTERVAL", 1.0)
    entity_ids = set(hass.states.async_entity_ids())
    password = entry.data[CONF_PASSWORD]
    result = await _reconfigure(hass, entry, _input(new_panel))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == new_panel.url
    assert entry.data[CONF_PASSWORD] == password
    assert entry.unique_id == f"{new_panel.host}:{new_panel.port}"
    assert entry.title == "Alarmanlage"
    assert entry.state is ConfigEntryState.LOADED
    assert set(hass.states.async_entity_ids()) == entity_ids
    assert fake_panel.stats.requests == ROUND
    assert new_panel.stats.requests == [("GET", "/system/"), *ROUND]


async def test_change_password_while_loaded(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new password is stored and the update listener reloads the entry.

    Home Assistant expects a flow not to reload an entry that has an update
    listener itself.
    """
    entry = await setup()
    reloads: list[object] = []
    monkeypatch.setattr(
        SecvestConfigFlow,
        "async_update_reload_and_abort",
        lambda self, *args, **kwargs: reloads.append(args),
    )
    old_coordinator = coordinator_of(entry)
    fake_panel.password = "new"
    result = await _reconfigure(hass, entry, _input(fake_panel, password="new"))
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.state is ConfigEntryState.LOADED
    assert coordinator_of(entry) is not old_coordinator
    assert reloads == []
    assert fake_panel.stats.requests[: len(ROUND) + 1] == [*ROUND, ("GET", "/system/")]


async def test_rejected_settings_keep_polling(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A rejected check changes nothing and isn't retried; polling goes on.

    The 401 of the check concerns the new credentials only: the entry's
    own aren't marked as failed and no reauthentication starts.
    """
    entry = await setup()
    data = dict(entry.data)
    coordinator = coordinator_of(entry)
    result = await _reconfigure(hass, entry, _input(fake_panel, password="wrong"))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert dict(entry.data) == data
    assert coordinator_of(entry) is coordinator
    assert not [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == SOURCE_REAUTH
    ]
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert fake_panel.stats.requests == [*ROUND, ("GET", "/system/"), *ROUND]
    # the entry's connection was closed for the check, so the round after it
    # needed a third one
    assert fake_panel.stats.connections == 3


async def test_unreachable_address(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An address nothing answers at gives an error; the entry stays."""
    entry = await setup()
    result = await _reconfigure(hass, entry, _input(fake_panel, url="127.0.0.1:1"))
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_URL] == fake_panel.url
    assert entry.state is ConfigEntryState.LOADED


async def test_check_holds_polling(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """While the panel is held, a polling round waits; the connection is closed."""
    entry = await setup()
    coordinator = coordinator_of(entry)
    async with coordinator.async_hold_panel():
        refresh = hass.async_create_task(coordinator.async_refresh())
        await asyncio.sleep(0.1)
        assert fake_panel.stats.requests == ROUND
        assert not refresh.done()
    await refresh
    assert fake_panel.stats.requests == [*ROUND, *ROUND]
    # the round after the hold needed a new connection
    assert fake_panel.stats.connections == 2


async def test_invalid_address(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An invalid address is refused before anything is sent."""
    entry = await setup()
    result = await _reconfigure(hass, entry, _input(fake_panel, url="http://panel"))
    assert result["errors"] == {CONF_URL: "invalid_address"}
    assert fake_panel.stats.requests == ROUND


async def test_address_of_another_entry(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An address another entry uses is refused before anything is sent."""
    entry = await setup()
    MockConfigEntry(domain=DOMAIN, unique_id="other:4433").add_to_hass(hass)
    result = await _reconfigure(hass, entry, _input(fake_panel, url="other"))
    assert result["errors"] == {CONF_URL: "already_configured"}
    assert fake_panel.stats.requests == ROUND


async def test_reconfigure_while_not_loaded(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """After a 401 at setup, new credentials load the entry and clear the flag."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    result = await _reconfigure(
        hass, entry, _input(fake_panel, password=fake_panel.password)
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_AUTH_FAILED] is False
    assert entry.state is ConfigEntryState.LOADED
    assert fake_panel.stats.requests == [("GET", "/system/"), *ROUND]
