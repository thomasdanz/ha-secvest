"""Config flow for the ABUS Secvest integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.typing import UNDEFINED, UndefinedType
from homeassistant.loader import async_get_integration

from .api.client import Client
from .api.errors import (
    AuthenticationError,
    CertificateError,
    CommunicationError,
    InstallerLockedError,
    SecvestError,
)
from .api.models import Partition, Zone
from .api.transport import PeerCertificate, Transport, probe_certificate
from .certificates import certificate_details, format_fingerprint
from .const import (
    CONF_ADVANCED,
    CONF_AUTH_FAILED,
    CONF_CERT_FINGERPRINT,
    CONF_CERTIFICATE_CHANGED,
    CONF_CODES,
    CONF_EXCLUDED_ZONES,
    CONF_PARTITIONS,
    CONF_USER_AGENT,
    CONF_USER_CODE,
    CONF_ZONE_DEVICE_CLASSES,
    DEFAULT_PORT,
    DOMAIN,
    SUBENTRY_ZONE_GROUP,
    TESTED_FIRMWARE,
    TESTED_MODEL,
)
from .flow_helpers import partition_selector, zone_labels, zone_options, zone_schema
from .options_flow import SecvestOptionsFlow
from .schema import vol
from .zone_group_flow import ZoneGroupFlow

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USER_CODE): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

STEP_RECONFIGURE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(),
        vol.Required(CONF_USER_CODE): TextSelector(),
        # empty keeps the stored password
        vol.Optional(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL): bool,
    }
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(),
        vol.Required(CONF_USER_CODE): TextSelector(),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_VERIFY_SSL, default=True): bool,
        vol.Required(CONF_ADVANCED): section(
            vol.Schema({vol.Optional(CONF_USER_AGENT, default=""): TextSelector()}),
            {"collapsed": True},
        ),
    }
)


def normalize_address(value: str) -> str:
    """Return the panel's address as https://host:port[/path].

    Without a scheme the panel's own port applies unless one is given; an
    https URL without a port means 443 (e.g. a reverse proxy).
    """
    value = value.strip()
    has_scheme = "://" in value
    parts = urlsplit(value if has_scheme else f"https://{value}")
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("not an https address")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("unexpected parts in the address")
    port = parts.port or (443 if has_scheme else DEFAULT_PORT)
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}:{port}{parts.path.rstrip('/')}"


def verification(data: Mapping[str, Any]) -> bool | str:
    """Return how the transport verifies the certificate (#149).

    Off: not at all. On: the pinned fingerprint of a self-signed
    certificate, or else the system's CA store.
    """
    if not data[CONF_VERIFY_SSL]:
        return False
    fingerprint: str | None = data.get(CONF_CERT_FINGERPRINT)
    return fingerprint or True


async def default_user_agent(hass: Any) -> str:
    """Return ha-secvest/<version> from the manifest."""
    integration = await async_get_integration(hass, DOMAIN)
    return f"ha-secvest/{integration.version}"


class SecvestConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a panel."""

    # stored data: VERSION changes break compatibility, MINOR_VERSION
    # changes don't; each step is migrated in async_migrate_entry
    VERSION = 1
    MINOR_VERSION = 4

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SecvestOptionsFlow:
        """Change the selection and the settings of a panel."""
        return SecvestOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Zone groups are subentries of the panel's entry."""
        return {SUBENTRY_ZONE_GROUP: ZoneGroupFlow}

    def __init__(self) -> None:
        """Start without a checked connection."""
        self._data: dict[str, Any] = {}
        self._title = ""
        self._partitions: list[Partition] = []
        self._zones: dict[str, Zone] = {}
        self._selected: list[int] = []
        # the last input, to show the form again after a later step failed
        self._user_input: dict[str, Any] | None = None
        # the certificate read before the credentials are sent (#149)
        self._probed: PeerCertificate | None = None
        # reconfigure: the unique id of the new address
        self._unique_id = ""
        # reauthentication: a confirmed certificate, stored with the
        # credentials if those are asked for too
        self._pending: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for address and credentials and check them once."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._user_input = user_input
            try:
                url = normalize_address(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_address"
            else:
                await self.async_set_unique_id(url.removeprefix("https://"))
                self._abort_if_unique_id_configured()
                user_agent = user_input[CONF_ADVANCED].get(CONF_USER_AGENT, "").strip()
                self._data = {
                    CONF_URL: url,
                    CONF_USER_CODE: user_input[CONF_USER_CODE].strip(),
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    CONF_CERT_FINGERPRINT: None,
                    CONF_USER_AGENT: user_agent,
                }
                if not self._data[CONF_VERIFY_SSL]:
                    return await self._check_setup()
                probed = await self._probe(url)
                if isinstance(probed, PeerCertificate):
                    return self._certificate_form(probed)
                errors["base"] = probed
        return self._user_form(errors)

    def _user_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, self._user_input
            ),
            errors=errors,
            description_placeholders={
                "model": TESTED_MODEL,
                "firmware": TESTED_FIRMWARE,
            },
        )

    async def _check_setup(self) -> ConfigFlowResult:
        error = await self._validate(self._data, read_partitions=True)
        if error is None:
            return await self.async_step_partitions()
        return self._user_form({"base": error})

    # the certificate of setup and reconfigure (#149): a public one is shown,
    # a self-signed one is confirmed and pinned; the credentials go out after

    def _certificate_form(self, probed: PeerCertificate) -> ConfigFlowResult:
        self._data[CONF_CERT_FINGERPRINT] = _pinned(probed)
        return self.async_show_form(
            step_id="certificate_public" if probed.public else "certificate_pinned",
            description_placeholders=certificate_details(
                probed.der, probed.fingerprint
            ),
        )

    async def async_step_certificate_public(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue after showing a publicly trusted certificate."""
        return await self._after_certificate()

    async def async_step_certificate_pinned(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Continue once the user confirmed a self-signed certificate."""
        return await self._after_certificate()

    async def _after_certificate(self) -> ConfigFlowResult:
        if self.source == SOURCE_RECONFIGURE:
            return await self._finish_reconfigure()
        return await self._check_setup()

    async def async_step_partitions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user select the partitions; the zones follow from them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = sorted(int(value) for value in user_input[CONF_PARTITIONS])
            if selected:
                self._selected = selected
                return await self.async_step_zones()
            errors["base"] = "no_partitions"
        # the panel doesn't reveal the user's rights, so all partitions are
        # offered; those without zones are deselected by default
        default = [str(p.number) for p in self._partitions if p.zone_ids]
        schema = vol.Schema(
            {
                vol.Required(CONF_PARTITIONS, default=default): partition_selector(
                    self._partitions
                )
            }
        )
        return self.async_show_form(
            step_id="partitions", data_schema=schema, errors=errors
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Exclude zones and choose a device class per zone, as in the options."""
        partitions = {p.number: p for p in self._partitions}
        zones = zone_labels(partitions, self._zones, self._selected)
        if user_input is not None or not zones:
            excluded, classes = zone_options(zones, user_input or {}, {})
            return self.async_create_entry(
                title=self._title,
                data=self._data,
                options={
                    CONF_PARTITIONS: self._selected,
                    CONF_EXCLUDED_ZONES: excluded,
                    CONF_ZONE_DEVICE_CLASSES: classes,
                    CONF_CODES: [],
                },
            )
        return self.async_show_form(
            step_id="zones", data_schema=zone_schema(zones, {}, [])
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start after the panel rejected the credentials or its certificate."""
        if entry_data.get(CONF_CERTIFICATE_CHANGED):
            return await self.async_step_reauth_certificate()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials and check them with exactly one request."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                **entry.data,
                **self._pending,
                CONF_USER_CODE: user_input[CONF_USER_CODE].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            error = await self._validate(data, read_partitions=False)
            if error is None:
                # the reload creates a new transport with these credentials
                return self._update_and_reload(
                    entry,
                    {
                        **self._pending,
                        CONF_USER_CODE: data[CONF_USER_CODE],
                        CONF_PASSWORD: data[CONF_PASSWORD],
                        CONF_AUTH_FAILED: False,
                    },
                )
            errors["base"] = error
        # the password is never suggested
        user_code = (user_input or entry.data)[CONF_USER_CODE]
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_SCHEMA, {CONF_USER_CODE: user_code}
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reauth_certificate(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Read the certificate the panel presents now; nothing is sent (#149).

        If it can't be read, submitting the form tries again.
        """
        entry = self._get_reauth_entry()
        probed = await self._probe(entry.data[CONF_URL], entry)
        if not isinstance(probed, PeerCertificate):
            return self.async_show_form(
                step_id="reauth_certificate",
                errors={"base": probed},
                description_placeholders={"name": entry.title},
            )
        stored = _stored_pin(entry.data)
        new = _pinned(probed)
        self._pending = {
            CONF_VERIFY_SSL: True,
            CONF_CERT_FINGERPRINT: new,
            CONF_CERTIFICATE_CHANGED: False,
        }
        if new == stored:
            # trusted again, e.g. a public certificate renewed meanwhile; still
            # confirmed, so a reload that fails again can't loop by itself
            step = "reauth_certificate_unchanged"
        elif probed.public:
            step = "reauth_certificate_now_public"
        elif stored is None:
            step = "reauth_certificate_now_pinned"
        else:
            step = "reauth_certificate_changed"
        return self.async_show_form(
            step_id=step,
            description_placeholders={
                "name": entry.title,
                "previous": format_fingerprint(stored or ""),
                **certificate_details(probed.der, probed.fingerprint),
            },
        )

    async def async_step_reauth_certificate_unchanged(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use the connection again: the trusted certificate is back."""
        return await self._confirm_certificate("certificate_unchanged")

    async def async_step_reauth_certificate_changed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pin the panel's new self-signed certificate."""
        return await self._confirm_certificate("certificate_confirmed")

    async def async_step_reauth_certificate_now_public(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify against the system's CA store instead of a pinned certificate."""
        return await self._confirm_certificate("certificate_confirmed")

    async def async_step_reauth_certificate_now_pinned(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pin a self-signed certificate instead of the system's CA store."""
        return await self._confirm_certificate("certificate_confirmed")

    async def _confirm_certificate(self, reason: str) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        if entry.data.get(CONF_AUTH_FAILED):
            # the credentials were rejected too: ask for them, then store both
            return await self.async_step_reauth_confirm()
        return self._update_and_reload(entry, self._pending, reason=reason)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change address, credentials or certificate check (#138).

        Checked with exactly one request; while the entry is loaded, its
        polling waits and its connection is closed meanwhile. Another panel
        at the new address isn't noticed: the API has no serial number. A
        certificate to be verified is read first and shown if it isn't the
        one stored (#149).
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._user_input = user_input
            try:
                url = normalize_address(user_input[CONF_URL])
            except ValueError:
                errors[CONF_URL] = "invalid_address"
            else:
                self._unique_id = url.removeprefix("https://")
                if any(
                    other.unique_id == self._unique_id
                    for other in self._async_current_entries(include_ignore=False)
                    if other.entry_id != entry.entry_id
                ):
                    errors[CONF_URL] = "already_configured"
                else:
                    self._data = {
                        **entry.data,
                        CONF_URL: url,
                        CONF_USER_CODE: user_input[CONF_USER_CODE].strip(),
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD)
                        or entry.data[CONF_PASSWORD],
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        CONF_CERT_FINGERPRINT: None,
                    }
                    if not self._data[CONF_VERIFY_SSL]:
                        return await self._finish_reconfigure()
                    probed = await self._probe(url, entry)
                    if not isinstance(probed, PeerCertificate):
                        errors["base"] = probed
                    elif entry.data[CONF_VERIFY_SSL] and _pinned(probed) == _stored_pin(
                        entry.data
                    ):
                        # verified as before: nothing to confirm
                        self._data[CONF_CERT_FINGERPRINT] = _pinned(probed)
                        return await self._finish_reconfigure()
                    else:
                        return self._certificate_form(probed)
        return self._reconfigure_form(errors)

    def _reconfigure_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        # the password is never suggested
        suggested = {
            key: (self._user_input or entry.data)[key]
            for key in (CONF_URL, CONF_USER_CODE, CONF_VERIFY_SSL)
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_RECONFIGURE_SCHEMA, suggested
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def _finish_reconfigure(self) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        if entry.state is ConfigEntryState.LOADED:
            async with entry.runtime_data.async_hold_panel():
                error = await self._validate(self._data, read_partitions=False)
        else:
            error = await self._validate(self._data, read_partitions=False)
        if error is not None:
            return self._reconfigure_form({"base": error})
        updates = {
            **self._data,
            CONF_AUTH_FAILED: False,
            CONF_CERTIFICATE_CHANGED: False,
        }
        return self._update_and_reload(entry, updates, unique_id=self._unique_id)

    def _update_and_reload(
        self,
        entry: ConfigEntry,
        updates: Mapping[str, Any],
        *,
        reason: str | UndefinedType = UNDEFINED,
        unique_id: str | UndefinedType = UNDEFINED,
    ) -> ConfigFlowResult:
        """Store the updates; the entry reloads with a new transport."""
        if entry.update_listeners:
            # loaded: its update listener reloads, as Home Assistant expects,
            # since the flags, the address, the credentials and the
            # certificate check are part of what it compares
            return self.async_update_and_abort(
                entry, data_updates=updates, reason=reason, unique_id=unique_id
            )
        # not loaded (e.g. a 401 at setup, or after a restart): nothing else
        # would reload it
        return self.async_update_reload_and_abort(
            entry, data_updates=updates, reason=reason, unique_id=unique_id
        )

    async def _probe(
        self, url: str, entry: ConfigEntry | None = None
    ) -> PeerCertificate | str:
        """Read the certificate, or return the error; nothing is sent.

        A loaded entry's polling waits meanwhile.
        """
        try:
            if entry is not None and entry.state is ConfigEntryState.LOADED:
                async with entry.runtime_data.async_hold_panel():
                    self._probed = await probe_certificate(url)
            else:
                self._probed = await probe_certificate(url)
        except SecvestError as err:
            return _error_key(err)
        return self._probed

    async def _validate(
        self, data: Mapping[str, Any], *, read_partitions: bool
    ) -> str | None:
        """Check the credentials, then read the partitions; never retried.

        The partitions are only read once the credentials were accepted.
        """
        transport = Transport(
            data[CONF_URL],
            data[CONF_USER_CODE],
            data[CONF_PASSWORD],
            verify=verification(data),
            user_agent=data[CONF_USER_AGENT] or await default_user_agent(self.hass),
        )
        if self._probed is not None and data[CONF_VERIFY_SSL]:
            # resumes the probe's session instead of another full handshake
            transport.resume(self._probed)
        try:
            client = Client(transport)
            system = await client.get_system()
            if read_partitions:
                self._partitions = await client.get_partitions()
                # for the zones step; only partitions that have zones, and on
                # the same connection
                for partition in self._partitions:
                    if partition.zone_ids:
                        for zone in await client.get_zones(partition.number):
                            self._zones.setdefault(zone.id, zone)
        except SecvestError as err:
            return _error_key(err)
        finally:
            await transport.close()
        self._title = system.name
        return None


def _error_key(err: SecvestError) -> str:
    """Return the form error for a failed check."""
    if isinstance(err, CertificateError):
        # another certificate than the one just read
        return "certificate_changed"
    if isinstance(err, AuthenticationError):
        return "invalid_auth"
    if isinstance(err, InstallerLockedError):
        return "installer_locked"
    if isinstance(err, CommunicationError):
        if isinstance(err.__cause__, TimeoutError):
            return "timeout"
        return "cannot_connect"
    return "unexpected_response"


def _pinned(probed: PeerCertificate) -> str | None:
    """Return the fingerprint to pin, or None for a publicly trusted one."""
    return None if probed.public else probed.fingerprint


def _stored_pin(data: Mapping[str, Any]) -> str | None:
    """Return the stored fingerprint; None means the system's CA store."""
    fingerprint: str | None = data.get(CONF_CERT_FINGERPRINT)
    return fingerprint
