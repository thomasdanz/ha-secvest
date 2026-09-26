"""Credentials never show up in logs or entity states (#50)."""

import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
import pytest

from custom_components.secvest.api.transport import basic_auth
from custom_components.secvest.const import CONF_USER_CODE

from .common import Setup, coordinator_of
from .fake_panel import FakePanel

USER_CODE = "918273"
PASSWORD = "s3cr3t-Pw-for-logs"


async def test_no_credentials_in_logs(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Setup, polling, a 401 and the reauthentication log no credentials."""
    caplog.set_level(logging.DEBUG)
    fake_panel.user_code = USER_CODE
    fake_panel.password = PASSWORD
    entry = await setup(password=PASSWORD, data={CONF_USER_CODE: USER_CODE})
    fake_panel.password = "rotated-" + PASSWORD
    await coordinator_of(entry).async_refresh()
    await hass.async_block_till_done()
    result = await entry.start_reauth_flow(hass)
    await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USER_CODE: USER_CODE, CONF_PASSWORD: "rotated-" + PASSWORD},
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    token = basic_auth(USER_CODE, PASSWORD).removeprefix("Basic ")
    states = str([state.as_dict() for state in hass.states.async_all()])
    for text in (caplog.text, states):
        assert USER_CODE not in text
        assert PASSWORD not in text
        assert token not in text
