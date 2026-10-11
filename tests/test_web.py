"""The web interface's zone status as entities (#56)."""

from datetime import timedelta
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.secvest import web as web_module
from custom_components.secvest.config_flow import _web_login_failed
from custom_components.secvest.const import (
    CONF_EXCLUDED_ZONES,
    CONF_USER_CODE,
    CONF_WEB_INTERFACE,
    CONF_WEB_INTERVAL,
    CONF_WEB_LOGIN_FAILED,
    DOMAIN,
)
from custom_components.secvest.diagnostics import async_get_config_entry_diagnostics
from custom_components.secvest.repairs import WebLoginRepairFlow, async_create_fix_flow
from custom_components.secvest.web import WebCoordinator, dbm_range

from .common import ROUND, Setup, coordinator_of, get_state, options_settings, state_of
from .fake_panel import FakePanel, Injection, WebRow

READ = [
    ("POST", "/sec_login.cgi"),
    ("POST", "/sec_zones.cgx"),
    ("POST", "/sec_about_panel.cgi"),
    ("POST", "/index.htm"),
]
SIGNAL = "sensor.alarmanlage_room_1_signal_strength"
REPEATER = "sensor.alarmanlage_room_1_signal_strength_repeater"
REPEATED = "sensor.alarmanlage_room_7_signal_strength_repeater"
BATTERY = "binary_sensor.alarmanlage_room_1_battery"
SUPERVISION = "binary_sensor.alarmanlage_room_1_supervision"
TAMPER = "binary_sensor.alarmanlage_room_1_tamper"
FAILED_LOGIN = Injection(
    "POST",
    "/sec_login.cgi",
    "status",
    status=200,
    body=b'<input type="hidden" value="0" id="ssid" /><li class="c warning-text">x',
    content_type="text/html",
)


@pytest.fixture(autouse=True)
def no_automatic_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read only when a test asks for it."""
    monkeypatch.setattr(web_module, "FIRST_WEB_DELAY", 3600)


def _web(entry: MockConfigEntry) -> WebCoordinator:
    web = coordinator_of(entry).web
    assert web is not None
    return web


async def _setup_web(setup: Setup, **options: Any) -> MockConfigEntry:
    return await setup(**{CONF_WEB_INTERFACE: True, **options})


def _web_requests(fake_panel: FakePanel) -> list[tuple[str, str]]:
    # the paths of a read, in the order they are sent
    return [r for r in fake_panel.stats.requests if r[1] in {p for _, p in READ}]


async def test_off_by_default(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Without the option: no entities and nothing sent to the web interface."""
    entry = await setup()
    assert coordinator_of(entry).web is None
    assert hass.states.get(SIGNAL) is None
    assert fake_panel.stats.requests == ROUND


async def test_first_read_after_setup(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Setup doesn't wait for the web interface; the first read follows."""
    fake_panel.web_rows["212"] = WebRow(level=0, lowest=0, repeater=(6, 2))
    fake_panel.web_rows["201"] = WebRow(level=4, lowest=1, battery_low=True)
    entry = await _setup_web(setup)
    assert fake_panel.stats.requests == ROUND
    assert state_of(hass, SIGNAL) == STATE_UNKNOWN
    assert state_of(hass, BATTERY) == STATE_UNKNOWN

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3601))
    await hass.async_block_till_done()
    assert _web_requests(fake_panel) == READ
    signal = get_state(hass, SIGNAL)
    assert signal.state == "4"
    assert signal.attributes["lowest_level"] == 1
    assert (signal.attributes["dbm_from"], signal.attributes["dbm_to"]) == (-92, -89)
    assert signal.attributes["last_read"] is not None
    assert state_of(hass, REPEATER) == STATE_UNKNOWN
    assert state_of(hass, REPEATED) == "6"
    assert get_state(hass, REPEATED).attributes["lowest_level"] == 2
    assert state_of(hass, BATTERY) == STATE_ON
    assert state_of(hass, SUPERVISION) == STATE_OFF
    assert state_of(hass, TAMPER) == STATE_OFF
    registry = er.async_get(hass)
    entity = registry.async_get(SIGNAL)
    assert entity is not None
    assert entity.unique_id == f"{entry.entry_id}_zone_201_signal_strength"
    assert entity.entity_category is EntityCategory.DIAGNOSTIC
    # on the zone's device
    zone = registry.async_get("binary_sensor.alarmanlage_room_1")
    assert zone is not None
    assert entity.device_id == zone.device_id
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (0, (None, -101)),
        (1, (-101, -98)),
        (4, (-92, -89)),
        (8, (-80, -77)),
        (9, (-77, None)),
    ],
)
def test_dbm_range(level: int, expected: tuple[int | None, int | None]) -> None:
    """The installer manual's table: 3 dB steps, open at both ends."""
    assert dbm_range(level) == expected


async def test_status_columns(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Supervision and tamper from their columns, never from REST faults."""
    fake_panel.web_rows["201"] = WebRow(supervision_fault=True, tamper=True)
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    assert state_of(hass, SUPERVISION) == STATE_ON
    assert state_of(hass, TAMPER) == STATE_ON
    assert state_of(hass, BATTERY) == STATE_OFF
    # the REST zone problem sensor is untouched by web values
    assert state_of(hass, "binary_sensor.alarmanlage_room_1_problem") == STATE_OFF


async def test_excluded_and_other_zones(hass: HomeAssistant, setup: Setup) -> None:
    """Excluded zones get no web entities; the others do."""
    await _setup_web(setup, **{CONF_EXCLUDED_ZONES: ["201"]})
    assert hass.states.get(SIGNAL) is None
    assert hass.states.get("sensor.alarmanlage_room_2_signal_strength") is not None


async def test_failed_login(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A failed login stops all web reads, also after a reload, until confirmed."""
    entry = await _setup_web(setup)
    fake_panel.inject(FAILED_LOGIN)
    await _web(entry).async_refresh()
    assert entry.data[CONF_WEB_LOGIN_FAILED] is True
    issue_id = f"web_{entry.entry_id}_login_failed"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert state_of(hass, SIGNAL) == STATE_UNAVAILABLE
    assert _web_requests(fake_panel) == [("POST", "/sec_login.cgi")]

    # nothing more: not on request, not at the interval, not after a reload
    await _web(entry).async_refresh()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=13))
    await hass.async_block_till_done()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _web(entry).async_refresh()
    assert _web_requests(fake_panel) == [("POST", "/sec_login.cgi")]
    assert state_of(hass, SIGNAL) == STATE_UNAVAILABLE
    # the REST API goes on as before
    assert coordinator_of(entry).last_update_success
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    # confirming in the repair issue allows one more login
    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    assert isinstance(flow, WebLoginRepairFlow)
    flow.hass = hass
    flow.issue_id = issue_id
    result = await flow.async_step_init()
    assert result["type"] is FlowResultType.FORM
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.data[CONF_WEB_LOGIN_FAILED] is False
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await _web(entry).async_refresh()
    assert _web_requests(fake_panel)[1:] == READ
    assert state_of(hass, SIGNAL) == "9"


def test_new_credentials_clear_the_failed_login() -> None:
    """Only a changed user code or password clears the flag (reauth, reconfigure)."""
    stored = {CONF_USER_CODE: "1234", CONF_PASSWORD: "old", CONF_WEB_LOGIN_FAILED: True}
    assert _web_login_failed(stored, {CONF_USER_CODE: "1234", CONF_PASSWORD: "old"})
    assert _web_login_failed(stored, {"url": "https://other"})
    assert not _web_login_failed(stored, {CONF_PASSWORD: "new"})
    assert not _web_login_failed(stored, {CONF_USER_CODE: "5678"})
    assert not _web_login_failed({CONF_PASSWORD: "old"}, {CONF_PASSWORD: "old"})


async def test_unusable_password(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A password with "&" can't log in at the web interface: nothing is sent."""
    fake_panel.password = "se&cret"
    entry = await _setup_web(setup, password="se&cret")
    issue_id = f"web_{entry.entry_id}_credentials"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert state_of(hass, SIGNAL) == STATE_UNAVAILABLE
    await _web(entry).async_refresh()
    assert _web_requests(fake_panel) == []


async def test_turning_off_removes_entities_and_issues(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Off in the options: the web entities and its issues go."""
    fake_panel.password = "se&cret"
    entry = await _setup_web(setup, password="se&cret")
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_WEB_INTERFACE: False}
    )
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert er.async_get(hass).async_get(SIGNAL) is None
    assert er.async_get(hass).async_get(BATTERY) is None
    assert not [
        issue_id
        for domain, issue_id in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith("web_")
    ]


async def test_installer_lock(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Nothing goes to the web interface while the installer is logged in."""
    entry = await _setup_web(setup)
    fake_panel.installer_locked = True
    await coordinator_of(entry).async_refresh()
    await _web(entry).async_refresh()
    assert _web_requests(fake_panel) == []


async def test_failed_read_keeps_the_values(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed read leaves the last values; the next interval tries again."""
    monkeypatch.setattr(web_module, "MIN_WEB_SPACING", 0)
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    for injection in (
        Injection("POST", "/sec_zones.cgx", "status", status=500),
        Injection("POST", "/sec_zones.cgx", "status", status=401),
    ):
        fake_panel.inject(injection)
        await _web(entry).async_refresh()
        assert state_of(hass, SIGNAL) == "9"
        assert _web(entry).last_error is not None
    # an ended session is no failed login
    assert not entry.data.get(CONF_WEB_LOGIN_FAILED)
    failed = [READ[0], READ[1], READ[3]]
    assert _web_requests(fake_panel) == READ + failed * 2
    await _web(entry).async_refresh()
    assert _web(entry).last_error is None


async def test_manual_read_keeps_a_spacing(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A manual read right after one sends nothing."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": SIGNAL}, blocking=True
    )
    await hass.async_block_till_done()
    assert _web_requests(fake_panel) == READ
    assert state_of(hass, SIGNAL) == "9"


async def test_reload_keeps_the_last_read(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A reload shows the last values at once and doesn't read again."""
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert state_of(hass, SIGNAL) == "9"
    assert _web_requests(fake_panel) == READ


async def test_reads_at_the_interval(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The configured interval schedules the reads, also across a reload."""

    async def _after(delay: timedelta) -> None:
        freezer.tick(delay)
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    entry = await _setup_web(setup, **{CONF_WEB_INTERVAL: 3})
    await _after(timedelta(seconds=3601))  # FIRST_WEB_DELAY in these tests
    assert _web_requests(fake_panel) == READ
    await _after(timedelta(hours=3) - timedelta(minutes=1))
    assert _web_requests(fake_panel) == READ
    await _after(timedelta(minutes=2))
    assert _web_requests(fake_panel) == READ * 2

    # a reload keeps the schedule: the next read comes 3 hours after the last
    await _after(timedelta(hours=1))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _after(timedelta(hours=2) - timedelta(minutes=2))
    assert _web_requests(fake_panel) == READ * 2
    await _after(timedelta(minutes=3))
    assert _web_requests(fake_panel) == READ * 3


async def test_options(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The interval is set in the options, in hours."""
    entry = await setup()
    result = await options_settings(hass, entry)
    schema = result["data_schema"]
    assert schema is not None
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in schema.schema
        if key.description
    }
    assert suggested[CONF_WEB_INTERFACE] is False
    assert suggested[CONF_WEB_INTERVAL] == 12
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "partitions": ["1"],
            "scan_interval": 30,
            "log_interval": 300,
            CONF_WEB_INTERFACE: True,
            CONF_WEB_INTERVAL: 3,
            "advanced": {},
        },
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert entry.options[CONF_WEB_INTERFACE] is True
    assert entry.options[CONF_WEB_INTERVAL] == 3
    assert _web(entry).update_interval == timedelta(hours=3)


async def test_options_refuse_an_unusable_password(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Turning it on with a password the web interface can't take is refused."""
    fake_panel.password = "se&cret"
    entry = await setup(password="se&cret")
    result = await options_settings(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "partitions": ["1"],
            "scan_interval": 30,
            "log_interval": 300,
            CONF_WEB_INTERFACE: True,
            CONF_WEB_INTERVAL: 12,
            "advanced": {},
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_WEB_INTERFACE: "web_credentials_ampersand"}


async def test_diagnostics(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """The web interface's state, without its values."""
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    state = _web(entry).as_dict()
    assert state["interval_hours"] == 12
    assert state["zones"] == 19
    assert state["login_failed"] is False
    assert state["last_read"] is not None
    # the serial number identifies the installation: not in the download
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["web_interface"]["firmware"] == "v3.01.31"
    assert "SECVEST0000TEST01" not in json.dumps(diagnostics, default=str)


async def test_unknown_values_are_logged_once(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A value the client doesn't know stays unknown and is logged once."""
    monkeypatch.setattr(web_module, "MIN_WEB_SPACING", 0)
    entry = await _setup_web(setup)
    body = fake_panel.zone_list().replace(
        b"<disp><id>rtm8_0</id><value>none</value>",
        b"<disp><id>rtm8_0</id><value>visible</value>",
    )
    for _ in range(2):
        fake_panel.inject(
            Injection(
                "POST",
                "/sec_zones.cgx",
                "status",
                status=200,
                body=body,
                content_type="text/xml",
            )
        )
        await _web(entry).async_refresh()
    assert state_of(hass, BATTERY) == STATE_UNKNOWN
    assert state_of(hass, SIGNAL) == "9"
    assert caplog.text.count("Unknown value from the web interface") == 1


async def test_not_after_a_rest_401(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """After the REST API's 401 nothing goes to the web interface either."""
    entry = await _setup_web(setup)
    fake_panel.password = "changed"
    await coordinator_of(entry).async_refresh()
    await hass.async_block_till_done()
    await _web(entry).async_refresh()
    assert _web_requests(fake_panel) == []
    assert not entry.data.get(CONF_WEB_LOGIN_FAILED)


async def test_confirming_for_a_removed_entry(hass: HomeAssistant) -> None:
    """Confirming after the entry was removed changes nothing."""
    flow = await async_create_fix_flow(hass, "web_x_login_failed", {"entry_id": "x"})
    assert isinstance(flow, WebLoginRepairFlow)
    flow.hass = hass
    flow.issue_id = "web_x_login_failed"
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_firmware_and_serial_number(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Info → Zentrale gives the panel device its firmware and serial (#194)."""
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    device = dr.async_get(hass).async_get(coordinator_of(entry).panel_device_id)
    assert isinstance(device, dr.DeviceEntry)
    assert device.sw_version == "v3.01.31"
    assert device.serial_number == "SECVEST0000TEST01"
    assert _web(entry).as_dict()["firmware"] == "v3.01.31"
    # the serial number identifies the installation: not in the diagnostics
    assert "TEST01" not in str(_web(entry).as_dict())
    issue_id = f"web_{entry.entry_id}_firmware"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_untested_firmware(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another firmware: logged once, an issue to ignore, nothing else changes."""
    monkeypatch.setattr(web_module, "MIN_WEB_SPACING", 0)
    fake_panel.firmware = "v3.02.01"
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    await _web(entry).async_refresh()
    issue_id = f"web_{entry.entry_id}_firmware"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["firmware"] == "v3.02.01"
    assert caplog.text.count("runs firmware v3.02.01") == 1
    assert state_of(hass, SIGNAL) == "9"
    # the tested one again: the issue goes
    fake_panel.firmware = "v3.01.31"
    await _web(entry).async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_info_page_without_values(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed or unreadable Info page keeps what the panel device shows."""
    monkeypatch.setattr(web_module, "MIN_WEB_SPACING", 0)
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    for injection in (
        Injection("POST", "/sec_about_panel.cgi", "status", status=500),
        Injection(
            "POST",
            "/sec_about_panel.cgi",
            "status",
            status=200,
            body=b"<html></html>",
            content_type="text/html",
        ),
    ):
        fake_panel.inject(injection)
        await _web(entry).async_refresh()
        device = dr.async_get(hass).async_get(coordinator_of(entry).panel_device_id)
        assert isinstance(device, dr.DeviceEntry)
        assert device.sw_version == "v3.01.31"
        assert device.serial_number == "SECVEST0000TEST01"
        assert state_of(hass, SIGNAL) == "9"


async def test_firmware_not_shown_keeps_the_device_value(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the serial number readable: the firmware shown stays."""
    monkeypatch.setattr(web_module, "MIN_WEB_SPACING", 0)
    entry = await _setup_web(setup)
    await _web(entry).async_refresh()
    fake_panel.firmware = "unknown"
    fake_panel.serial_number = "SECVEST0000TEST02"
    await _web(entry).async_refresh()
    device = dr.async_get(hass).async_get(coordinator_of(entry).panel_device_id)
    assert isinstance(device, dr.DeviceEntry)
    assert device.sw_version == "v3.01.31"
    assert device.serial_number == "SECVEST0000TEST02"
