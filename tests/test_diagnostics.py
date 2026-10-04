"""Redacted diagnostics download (#43)."""

import json
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.const import (
    CONF_AUTH_FAILED,
    CONF_INSTALLATION_NAME,
    SUBENTRY_ZONE_GROUP,
)
from custom_components.secvest.diagnostics import async_get_config_entry_diagnostics

from .common import CODE, ROUND, Setup
from .fake_panel import FakePanel


async def _diagnostics(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    return await async_get_config_entry_diagnostics(hass, entry)


async def test_content(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Raw answers, connection, polling, log and the tested firmware."""
    entry = await setup(code=CODE)
    sent = list(fake_panel.stats.requests)
    diagnostics = await _diagnostics(hass, entry)
    # nothing is sent for it
    assert fake_panel.stats.requests == sent
    assert diagnostics["tested"] == {
        "model": "Secvest Touch FUAA50500",
        "firmware": "v3.01.31",
    }
    assert diagnostics["entry"]["version"] == "1.3"
    assert diagnostics["entry"]["options"]["codes"] == [{"kdf": "pbkdf2-sha256-100000"}]
    assert diagnostics["transport"]["requests"] == len(ROUND)
    assert diagnostics["transport"]["full_handshakes"] >= 1
    assert diagnostics["polling"]["backoff"]["consecutive_failures"] == 0
    assert diagnostics["polling"]["selected_partitions"] == [1]
    assert diagnostics["log"]["has_baseline"] is False
    # the raw answers of the round, by path, with ids and states
    assert set(diagnostics["responses"]) == {path for _, path in ROUND}
    zones = diagnostics["responses"]["/system/partitions-1/zones/"]
    assert {zone["id"] for zone in zones} == set(fake_panel.partitions[1].zone_ids)
    assert all(zone["name"] == "**REDACTED**" for zone in zones)
    json.dumps(diagnostics)


async def test_nothing_identifying(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Address, credentials, codes, names and texts are redacted."""
    # an open zone is listed as a fault whose text names the zone
    fake_panel.zones["209"].state = "open"
    fake_panel.static_faults.append(
        {"type": "1170", "id": "1104", "ui-string": "REP01 Batt schwach"}
    )
    entry = await setup(code=CODE, data={CONF_INSTALLATION_NAME: "Butterkeks"})
    assert "209" in json.dumps(entry.runtime_data.client.transport.last_responses)
    hass.config_entries.async_add_subentry(
        entry,
        _group_subentry("Gartenhaus", ["203", "204"]),
    )
    await hass.async_block_till_done()
    dump = json.dumps(await _diagnostics(hass, entry))
    stored_code = entry.options["codes"][0]
    # short numbers could occur in measurements, so they are looked for as
    # whole JSON values
    for number in (fake_panel.user_code, CODE):
        assert f'"{number}"' not in dump, number
    secrets = [
        fake_panel.url,
        fake_panel.host,
        fake_panel.password,
        stored_code["salt"],
        stored_code["hash"],
        "Tester",
        "Butterkeks",
        "Gartenhaus",
        fake_panel.name,
        *(partition.name for partition in fake_panel.partitions.values()),
        *(zone.name for zone in fake_panel.zones.values()),
        # fault texts name zones
        "Batt schwach",
    ]
    for secret in secrets:
        assert secret not in dump, secret


def _group_subentry(name: str, zones: list[str]) -> ConfigSubentry:
    return ConfigSubentry(
        data=MappingProxyType({"name": name, "zones": zones, "device_class": "window"}),
        subentry_type=SUBENTRY_ZONE_GROUP,
        title=name,
        unique_id=None,
    )


async def test_not_loaded(hass: HomeAssistant, setup: Setup) -> None:
    """Without a loaded entry, the stored settings only."""
    entry = await setup(data={CONF_AUTH_FAILED: True})
    diagnostics = await _diagnostics(hass, entry)
    assert set(diagnostics) == {"tested", "entry"}
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
