"""Tests for the config flow (#38)."""

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.api.errors import CommunicationError
from custom_components.secvest.config_flow import normalize_address
from custom_components.secvest.const import (
    CONF_ADVANCED,
    CONF_PARTITIONS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    DOMAIN,
)

from .fake_panel import FakePanel, Injection

MANIFEST = Path(__file__).parent.parent / "custom_components/secvest/manifest.json"


@pytest.fixture(autouse=True)
def no_setup() -> Iterator[None]:
    """Only the flow is tested here; setup is tested with the coordinator."""
    with patch("custom_components.secvest.async_setup_entry", return_value=True):
        yield


def _input(panel: FakePanel, **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_URL: f"{panel.host}:{panel.port}",
        CONF_USER_CODE: panel.user_code,
        CONF_PASSWORD: panel.password,
        CONF_VERIFY_SSL: False,
        CONF_ADVANCED: {},
    }
    data.update(changes)
    return data


async def _submit(hass: HomeAssistant, user_input: dict[str, Any]) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["description_placeholders"] == {
        "model": "Secvest Touch FUAA50500",
        "firmware": "v3.01.31",
    }
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def _select(hass: HomeAssistant, result: Any, partitions: list[str]) -> Any:
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "partitions"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PARTITIONS: partitions}
    )


async def test_create_entry(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """Valid credentials lead to the selection, then create the entry."""
    result = await _submit(hass, _input(fake_panel))
    result = await _select(hass, result, ["1"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Alarmanlage"
    assert result["data"] == {
        CONF_URL: f"https://{fake_panel.host}:{fake_panel.port}",
        CONF_USER_CODE: fake_panel.user_code,
        CONF_PASSWORD: fake_panel.password,
        CONF_VERIFY_SSL: False,
        CONF_USER_AGENT: "",
    }
    assert result["options"] == {CONF_PARTITIONS: [1]}
    assert result["result"].unique_id == f"{fake_panel.host}:{fake_panel.port}"
    # both reads share one connection; the selection sends nothing
    assert fake_panel.stats.requests == [
        ("GET", "/system/"),
        ("GET", "/system/partitions/"),
    ]
    assert fake_panel.stats.connections == 1
    version = json.loads(MANIFEST.read_text())["version"]
    assert fake_panel.stats.user_agents == [f"ha-secvest/{version}"] * 2


async def test_partitions_offered(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """All partitions are offered; those without zones are deselected."""
    result = await _submit(hass, _input(fake_panel))
    schema = result["data_schema"].schema
    (key,) = schema
    assert key == CONF_PARTITIONS
    assert key.default() == ["1"]
    options = schema[key].config["options"]
    assert [option["value"] for option in options] == ["1", "2", "3", "4"]
    assert options[1]["label"] == "2: Teilber. 2"


async def test_several_partitions(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """Several partitions are stored as sorted numbers."""
    result = await _submit(hass, _input(fake_panel))
    result = await _select(hass, result, ["3", "1"])
    assert result["options"] == {CONF_PARTITIONS: [1, 3]}


async def test_no_partition_selected(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """At least one partition has to be selected; nothing is sent again."""
    result = await _submit(hass, _input(fake_panel))
    result = await _select(hass, result, [])
    assert result["errors"] == {"base": "no_partitions"}
    result = await _select(hass, result, ["1"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(fake_panel.stats.requests) == 2


async def test_user_agent_override(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """A User-Agent from the advanced section is used and stored."""
    result = await _submit(
        hass, _input(fake_panel, **{CONF_ADVANCED: {CONF_USER_AGENT: " Proxy/1 "}})
    )
    result = await _select(hass, result, ["1"])
    assert result["data"][CONF_USER_AGENT] == "Proxy/1"
    assert fake_panel.stats.user_agents == ["Proxy/1"] * 2


async def test_invalid_auth(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """Wrong credentials are reported after one request, then corrected."""
    result = await _submit(hass, _input(fake_panel, **{CONF_PASSWORD: "wrong"}))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    # the partitions aren't read with rejected credentials
    assert fake_panel.stats.requests == [("GET", "/system/")]
    # nothing is retried; the user corrects the password
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _input(fake_panel)
    )
    result = await _select(hass, result, ["1"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(fake_panel.stats.requests) == 3


async def test_partitions_unreadable(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """If the partitions can't be read, setup stays on the first step."""
    fake_panel.inject(Injection("GET", "/system/partitions/", "drop_before", times=2))
    result = await _submit(hass, _input(fake_panel))
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_installer_locked(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """The installer lock has its own message."""
    fake_panel.installer_locked = True
    result = await _submit(hass, _input(fake_panel))
    assert result["errors"] == {"base": "installer_locked"}
    assert len(fake_panel.stats.requests) == 1


@pytest.mark.usefixtures("socket_enabled")
async def test_cannot_connect(hass: HomeAssistant) -> None:
    """An unreachable panel is reported."""
    result = await _submit(
        hass,
        {
            CONF_URL: "127.0.0.1:1",
            CONF_USER_CODE: "1",
            CONF_PASSWORD: "x",
            CONF_VERIFY_SSL: False,
            CONF_ADVANCED: {},
        },
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_certificate_rejected(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """With verification on, the self-signed certificate can't connect."""
    result = await _submit(hass, _input(fake_panel, **{CONF_VERIFY_SSL: True}))
    assert result["errors"] == {"base": "cannot_connect"}
    assert fake_panel.stats.requests == []


async def test_timeout(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """A timeout has its own message."""
    error = CommunicationError("timed out")
    error.__cause__ = TimeoutError()
    with patch(
        "custom_components.secvest.config_flow.Client.get_system", side_effect=error
    ):
        result = await _submit(hass, _input(fake_panel))
    assert result["errors"] == {"base": "timeout"}


async def test_unexpected_response(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """Something that isn't a Secvest panel is reported as such."""
    url = f"https://{fake_panel.host}:{fake_panel.port}/wrong"
    result = await _submit(hass, _input(fake_panel, **{CONF_URL: url}))
    assert result["errors"] == {"base": "unexpected_response"}
    fake_panel.violations.clear()


@pytest.mark.parametrize(
    "address",
    [
        "http://panel",
        "",
        "https://",
        "a b:x",
        # credentials, queries and fragments don't belong in the address
        "https://user:pw@panel",
        "https://panel/?x=1",
        "https://panel/#x",
    ],
)
async def test_invalid_address(hass: HomeAssistant, address: str) -> None:
    """An address that isn't usable is refused without a request."""
    result = await _submit(
        hass,
        {
            CONF_URL: address,
            CONF_USER_CODE: "1",
            CONF_PASSWORD: "x",
            CONF_VERIFY_SSL: False,
            CONF_ADVANCED: {},
        },
    )
    assert result["errors"] == {CONF_URL: "invalid_address"}


async def test_already_configured(hass: HomeAssistant, fake_panel: FakePanel) -> None:
    """The same panel can't be set up twice, whatever the spelling."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=f"{fake_panel.host}:{fake_panel.port}"
    ).add_to_hass(hass)
    url = f"HTTPS://{fake_panel.host.upper()}:{fake_panel.port}/"
    result = await _submit(hass, _input(fake_panel, **{CONF_URL: url}))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert fake_panel.stats.requests == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("192.168.1.10", "https://192.168.1.10:4433"),
        (" 192.168.1.10:4433 ", "https://192.168.1.10:4433"),
        ("Panel.Local:8443", "https://panel.local:8443"),
        ("https://alarm.example.com", "https://alarm.example.com:443"),
        ("https://alarm.example.com/secvest/", "https://alarm.example.com:443/secvest"),
        ("[::1]:4433", "https://[::1]:4433"),
    ],
)
def test_normalize_address(value: str, expected: str) -> None:
    """Addresses are normalised; without a scheme the panel's port applies."""
    assert normalize_address(value) == expected
