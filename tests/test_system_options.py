"""Home Assistant's system options for the entry, end to end."""

from datetime import timedelta

from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.secvest.const import (
    CONF_PARTITIONS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    DOMAIN,
)

from .common import ROUND
from .fake_panel import FakePanel


async def _setup(
    hass: HomeAssistant, panel: FakePanel, *, polling: bool
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=panel.name,
        data={
            CONF_URL: panel.url,
            CONF_USER_CODE: panel.user_code,
            CONF_PASSWORD: panel.password,
            CONF_VERIFY_SSL: False,
            CONF_USER_AGENT: "",
        },
        options={CONF_PARTITIONS: [1]},
        pref_disable_polling=not polling,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(("polling", "rounds"), [(True, 2), (False, 1)])
async def test_polling_option(
    hass: HomeAssistant, fake_panel: FakePanel, polling: bool, rounds: int
) -> None:
    """With polling off, only setup reads the panel on its own."""
    entry = await _setup(hass, fake_panel, polling=polling)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    # the coordinator runs scheduled rounds as background tasks
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fake_panel.stats.requests == ROUND * rounds
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_manual_refresh_with_polling_off(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """With polling off, update_entity still runs a round."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = await _setup(hass, fake_panel, polling=False)
    await hass.services.async_call(
        "homeassistant",
        "update_entity",
        {"entity_id": "binary_sensor.alarmanlage_installer_lock"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert fake_panel.stats.requests == ROUND * 2
    assert await hass.config_entries.async_unload(entry.entry_id)
