"""Tests for the verification of the panel's certificate (#149)."""

from collections.abc import Callable, Iterator
import json
from pathlib import Path
import ssl
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.api.errors import CommunicationError
from custom_components.secvest.api.transport import fingerprint, probe_certificate
from custom_components.secvest.certificates import (
    certificate_details,
    format_fingerprint,
)
from custom_components.secvest.const import (
    CONF_ADVANCED,
    CONF_AUTH_FAILED,
    CONF_CERT_FINGERPRINT,
    CONF_CERTIFICATE_CHANGED,
    CONF_PARTITIONS,
    CONF_USER_CODE,
    DOMAIN,
)
from custom_components.secvest.diagnostics import async_get_config_entry_diagnostics

from .common import ROUND, Setup, call_panel, coordinator_of, state_of
from .fake_panel import FakePanel

type Certificate = tuple[Path, Path]
type Trust = Callable[[Certificate], None]


def _fingerprint(certificate: Certificate) -> str:
    return fingerprint(ssl.PEM_cert_to_DER_cert(certificate[0].read_text()))


@pytest.fixture
def trust(monkeypatch: pytest.MonkeyPatch) -> Trust:
    """Make a certificate publicly trusted, as if a public CA had signed it."""

    def _trust(certificate: Certificate) -> None:
        monkeypatch.setenv("SSL_CERT_FILE", str(certificate[0]))

    return _trust


def _pinned(certificate: Certificate) -> dict[str, Any]:
    return {CONF_VERIFY_SSL: True, CONF_CERT_FINGERPRINT: _fingerprint(certificate)}


def _flows(hass: HomeAssistant, source: str = SOURCE_REAUTH) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == source
    ]


async def _reauth_step(hass: HomeAssistant) -> Any:
    """Return the reauthentication flow Home Assistant started, at its step."""
    await hass.async_block_till_done()
    (flow,) = _flows(hass)
    return flow


async def _reauth_form(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Start the reauthentication again, for the form it shows."""
    for flow in _flows(hass):
        hass.config_entries.flow.async_abort(flow["flow_id"])
    return await entry.start_reauth_flow(hass)


async def _confirm(hass: HomeAssistant, flow: Any) -> Any:
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    await hass.async_block_till_done()
    return result


# setup


@pytest.fixture
def no_setup() -> Iterator[None]:
    """Only the flow is tested; setup is tested with the coordinator."""
    with patch("custom_components.secvest.async_setup_entry", return_value=True):
        yield


async def _start_setup(hass: HomeAssistant, panel: FakePanel, **changes: Any) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: f"{panel.host}:{panel.port}",
            CONF_USER_CODE: panel.user_code,
            CONF_PASSWORD: panel.password,
            CONF_VERIFY_SSL: True,
            CONF_ADVANCED: {},
            **changes,
        },
    )


async def _finish_setup(hass: HomeAssistant, result: Any) -> Any:
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "partitions"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PARTITIONS: ["1"]}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], {})


@pytest.mark.usefixtures("no_setup")
async def test_setup_pins_a_self_signed_certificate(
    hass: HomeAssistant, fake_panel: FakePanel, panel_certificate: Certificate
) -> None:
    """The check is on by default; a self-signed certificate is confirmed first.

    Nothing is sent before the confirmation, and the check afterwards
    resumes the session of the probe instead of another full handshake.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    schema = result["data_schema"]
    assert schema is not None
    (verify,) = (key for key in schema.schema if key == CONF_VERIFY_SSL)
    assert verify.default() is True
    result = await _start_setup(hass, fake_panel)
    assert result["step_id"] == "certificate_pinned"
    assert result["description_placeholders"]["fingerprint"] == format_fingerprint(
        _fingerprint(panel_certificate)
    )
    assert result["description_placeholders"]["subject"] == "CN=secvest"
    assert fake_panel.stats.requests == []
    result = await _finish_setup(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VERIFY_SSL] is True
    assert result["data"][CONF_CERT_FINGERPRINT] == _fingerprint(panel_certificate)
    assert fake_panel.stats.full_handshakes == 1
    assert fake_panel.stats.resumed_handshakes == 1


@pytest.mark.usefixtures("no_setup")
async def test_setup_shows_a_public_certificate(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    panel_certificate: Certificate,
    trust: Trust,
) -> None:
    """A publicly trusted certificate is only shown, and not pinned."""
    trust(panel_certificate)
    result = await _start_setup(hass, fake_panel)
    assert result["step_id"] == "certificate_public"
    result = await _finish_setup(hass, result)
    assert result["data"][CONF_VERIFY_SSL] is True
    assert result["data"][CONF_CERT_FINGERPRINT] is None


@pytest.mark.usefixtures("no_setup")
async def test_setup_certificate_unreadable(
    hass: HomeAssistant, fake_panel: FakePanel
) -> None:
    """If the certificate can't be read, the address form shows why."""
    result = await _start_setup(hass, fake_panel, **{CONF_URL: "127.0.0.1:1"})
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("no_setup")
async def test_setup_certificate_changed_before_the_check(
    hass: HomeAssistant, fake_panel: FakePanel, other_certificate: Certificate
) -> None:
    """Another certificate at the check than the one confirmed sends nothing."""
    result = await _start_setup(hass, fake_panel)
    fake_panel.swap_certificate(*other_certificate)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "certificate_changed"}
    assert fake_panel.stats.requests == []


# a changed certificate while running, like a 401


async def test_changed_certificate_stops_everything(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
) -> None:
    """Nothing is sent after another certificate, until the user confirms it."""
    entry = await setup(data=_pinned(panel_certificate))
    fake_panel.swap_certificate(*other_certificate)
    await coordinator_of(entry).async_refresh()
    await _reauth_step(hass)
    assert entry.data[CONF_CERTIFICATE_CHANGED] is True
    assert CONF_AUTH_FAILED not in entry.data
    assert state_of(hass) == "unavailable"
    flow = await _reauth_form(hass, entry)
    assert flow["step_id"] == "reauth_certificate_changed"
    placeholders = flow["description_placeholders"]
    assert placeholders["previous"] == format_fingerprint(
        _fingerprint(panel_certificate)
    )
    assert placeholders["fingerprint"] == format_fingerprint(
        _fingerprint(other_certificate)
    )
    await coordinator_of(entry).async_refresh()
    assert fake_panel.stats.requests == ROUND

    result = await _confirm(hass, flow)
    assert result["reason"] == "certificate_confirmed"
    assert entry.data[CONF_CERT_FINGERPRINT] == _fingerprint(other_certificate)
    assert entry.data[CONF_CERTIFICATE_CHANGED] is False
    assert entry.state is ConfigEntryState.LOADED
    # the new transport trusts the new certificate
    await coordinator_of(entry).async_refresh()
    assert fake_panel.stats.requests[-len(ROUND) :] == ROUND
    assert coordinator_of(entry).last_update_success


async def test_nothing_sent_after_a_restart(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    other_certificate: Certificate,
) -> None:
    """With the flag set, setup sends nothing and asks for the certificate."""
    entry = await setup(
        data={**_pinned(other_certificate), CONF_CERTIFICATE_CHANGED: True}
    )
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert fake_panel.stats.requests == []
    assert len(_flows(hass)) == 1


async def test_public_certificate_becomes_self_signed(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
    trust: Trust,
) -> None:
    """A certificate that no longer verifies is confirmed, then pinned."""
    trust(panel_certificate)
    entry = await setup(data={CONF_VERIFY_SSL: True})
    fake_panel.swap_certificate(*other_certificate)
    await coordinator_of(entry).async_refresh()
    flow = await _reauth_step(hass)
    assert flow["step_id"] == "reauth_certificate_now_pinned"
    result = await _confirm(hass, flow)
    assert result["reason"] == "certificate_confirmed"
    assert entry.data[CONF_CERT_FINGERPRINT] == _fingerprint(other_certificate)
    assert entry.state is ConfigEntryState.LOADED


async def test_pinned_certificate_becomes_public(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
    trust: Trust,
) -> None:
    """A publicly trusted certificate instead of the pinned one is confirmed too."""
    entry = await setup(data=_pinned(panel_certificate))
    fake_panel.swap_certificate(*other_certificate)
    trust(other_certificate)
    await coordinator_of(entry).async_refresh()
    flow = await _reauth_step(hass)
    assert flow["step_id"] == "reauth_certificate_now_public"
    result = await _confirm(hass, flow)
    assert result["reason"] == "certificate_confirmed"
    assert entry.data[CONF_VERIFY_SSL] is True
    assert entry.data[CONF_CERT_FINGERPRINT] is None
    assert entry.state is ConfigEntryState.LOADED


async def test_trusted_certificate_again(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
) -> None:
    """If the trusted certificate is back, one click uses the connection again.

    Not without the click: a reload that failed again would start the next
    confirmation by itself, in a loop of handshakes.
    """
    entry = await setup(
        data={**_pinned(panel_certificate), CONF_CERTIFICATE_CHANGED: True}
    )
    flow = await _reauth_step(hass)
    assert flow["step_id"] == "reauth_certificate_unchanged"
    assert entry.data[CONF_CERTIFICATE_CHANGED] is True
    assert fake_panel.stats.requests == []
    result = await _confirm(hass, flow)
    assert result["reason"] == "certificate_unchanged"
    assert entry.data[CONF_CERTIFICATE_CHANGED] is False
    assert entry.state is ConfigEntryState.LOADED
    assert fake_panel.stats.requests == ROUND


async def test_trusted_certificate_back_while_loaded(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
) -> None:
    """Clearing the flag alone reloads a loaded entry, which polls again."""
    entry = await setup(data=_pinned(panel_certificate))
    coordinator = coordinator_of(entry)
    fake_panel.swap_certificate(*other_certificate)
    await coordinator.async_refresh()
    fake_panel.swap_certificate(*panel_certificate)
    flow = await _reauth_form(hass, entry)
    assert flow["step_id"] == "reauth_certificate_unchanged"
    result = await _confirm(hass, flow)
    assert result["reason"] == "certificate_unchanged"
    assert coordinator_of(entry) is not coordinator
    await coordinator_of(entry).async_refresh()
    assert coordinator_of(entry).last_update_success


async def test_certificate_unreadable_at_the_confirmation(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
) -> None:
    """If the certificate can't be read, submitting the form tries again."""
    fake_panel.swap_certificate(*other_certificate)
    with patch(
        "custom_components.secvest.config_flow.probe_certificate",
        side_effect=CommunicationError("refused"),
    ):
        entry = await setup(
            data={**_pinned(panel_certificate), CONF_CERTIFICATE_CHANGED: True}
        )
        flow = await _reauth_step(hass)
    assert flow["step_id"] == "reauth_certificate"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
    assert result["step_id"] == "reauth_certificate_changed"
    result = await _confirm(hass, result)
    assert entry.state is ConfigEntryState.LOADED


async def test_certificate_and_credentials(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    other_certificate: Certificate,
) -> None:
    """With the credentials rejected too, they are asked for after the certificate.

    Both are stored together once the credentials are accepted.
    """
    entry = await setup(
        password="old",
        data={
            **_pinned(other_certificate),
            CONF_CERTIFICATE_CHANGED: True,
            CONF_AUTH_FAILED: True,
        },
    )
    flow = await _reauth_step(hass)
    assert flow["step_id"] == "reauth_certificate_changed"
    result = await _confirm(hass, flow)
    assert result["step_id"] == "reauth_confirm"
    assert entry.data[CONF_CERTIFICATE_CHANGED] is True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USER_CODE: fake_panel.user_code, CONF_PASSWORD: fake_panel.password},
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CERTIFICATE_CHANGED] is False
    assert entry.data[CONF_AUTH_FAILED] is False
    assert entry.data[CONF_PASSWORD] == fake_panel.password
    assert entry.state is ConfigEntryState.LOADED


async def test_command_after_a_certificate_change(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
) -> None:
    """A command names the certificate, not the credentials."""
    await setup(data=_pinned(panel_certificate))
    fake_panel.swap_certificate(*other_certificate)
    with pytest.raises(HomeAssistantError) as raised:
        await call_panel(hass, "alarm_arm_away", code=None)
    assert raised.value.translation_key == "certificate_changed"
    assert fake_panel.stats.requests == ROUND


async def test_take_over_names_after_a_certificate_change(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    other_certificate: Certificate,
) -> None:
    """Taking over names stops at the certificate, like at a 401."""
    entry = await setup(data=_pinned(panel_certificate))
    fake_panel.swap_certificate(*other_certificate)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "names"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["reason"] == "certificate_changed"
    assert entry.data[CONF_CERTIFICATE_CHANGED] is True


# reconfigure (#138)


async def _reconfigure(
    hass: HomeAssistant, entry: MockConfigEntry, panel: FakePanel, verify: bool
) -> Any:
    result = await entry.start_reconfigure_flow(hass)
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: panel.url,
            CONF_USER_CODE: panel.user_code,
            CONF_VERIFY_SSL: verify,
        },
    )


async def test_reconfigure_turns_the_check_on(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
) -> None:
    """Turning the check on shows the certificate; it is pinned once confirmed."""
    entry = await setup()
    result = await _reconfigure(hass, entry, fake_panel, verify=True)
    assert result["step_id"] == "certificate_pinned"
    result = await _confirm(hass, result)
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_VERIFY_SSL] is True
    assert entry.data[CONF_CERT_FINGERPRINT] == _fingerprint(panel_certificate)
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_same_certificate(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
) -> None:
    """The certificate stored is not shown again; a flag is cleared."""
    entry = await setup(
        data={**_pinned(panel_certificate), CONF_CERTIFICATE_CHANGED: True}
    )
    result = await _reconfigure(hass, entry, fake_panel, verify=True)
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CERTIFICATE_CHANGED] is False
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_public_certificate(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
    trust: Trust,
) -> None:
    """A public certificate is shown when switching from a pinned one."""
    entry = await setup(data=_pinned(panel_certificate))
    coordinator = coordinator_of(entry)
    trust(panel_certificate)
    result = await _reconfigure(hass, entry, fake_panel, verify=True)
    assert result["step_id"] == "certificate_public"
    result = await _confirm(hass, result)
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_CERT_FINGERPRINT] is None
    # reloaded: the new transport verifies the other way
    assert coordinator_of(entry) is not coordinator


async def test_reconfigure_certificate_unreadable(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """If the certificate can't be read, the form shows why; nothing changes."""
    entry = await setup()
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: "127.0.0.1:1",
            CONF_USER_CODE: fake_panel.user_code,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_VERIFY_SSL] is False


async def test_reconfigure_rejected_after_the_certificate(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed check after the certificate step shows the form again."""
    entry = await setup()
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_URL: fake_panel.url,
            CONF_USER_CODE: fake_panel.user_code,
            CONF_PASSWORD: "wrong",
            CONF_VERIFY_SSL: True,
        },
    )
    result = await _confirm(hass, result)
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_VERIFY_SSL] is False


# shown, never decided on


async def test_fingerprint_redacted_in_diagnostics(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    panel_certificate: Certificate,
) -> None:
    """The fingerprint identifies the panel, so diagnostics leave it out."""
    entry = await setup(data=_pinned(panel_certificate))
    dump = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    assert _fingerprint(panel_certificate) not in dump


async def test_details(fake_panel: FakePanel) -> None:
    """Subject, issuer, validity and the fingerprint in pairs."""
    probed = await probe_certificate(fake_panel.url)
    details = certificate_details(probed.der, probed.fingerprint)
    assert details["subject"] == details["issuer"] == "CN=secvest"
    assert len(details["valid_from"]) == len("2026-10-05")
    assert details["fingerprint"].count(":") == 31


def test_details_of_an_unreadable_certificate() -> None:
    """Something that isn't a certificate still shows its fingerprint."""
    details = certificate_details(b"nonsense", "ab" * 32)
    assert details["subject"] == "?"
    assert details["fingerprint"] == ":".join(["AB"] * 32)
