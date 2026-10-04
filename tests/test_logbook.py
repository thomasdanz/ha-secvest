"""Log entries in the real logbook, read back from the database (#167)."""

from datetime import timedelta

from homeassistant.components.logbook.processor import EventProcessor
from homeassistant.components.recorder.core import Recorder
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import mock_component
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from pytest_homeassistant_custom_component.plugins import (
    RecorderInstanceContextManager,
)

from custom_components.secvest.event import EVENT_LOG_ENTRY

from .common import Setup, get_state, poll_log_now
from .fake_panel import FakePanel

LOG = "event.alarmanlage_log"


@pytest.fixture
async def mock_recorder_before_hass(
    async_test_recorder: RecorderInstanceContextManager,
) -> None:
    """Set up the recorder's database before Home Assistant."""


async def test_logbook_row_with_the_text(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    log_now: None,
) -> None:
    """The real logbook shows the entry's text with the panel's time (#167).

    The logbook reads the event back from the database and passes only part
    of it to the integration's description.
    """
    mock_component(hass, "frontend")
    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "logbook", {})
    # the panel's clock runs in Home Assistant's time zone, so the entry is
    # from today whatever the time of day (#8)
    offset = dt_util.now().utcoffset()
    assert offset is not None
    fake_panel.clock_offset = int(offset.total_seconds())
    entry = await setup()
    start = dt_util.utcnow()
    fake_panel.add_log_entry("Ben 003 TB 1 aktiv", partition="0", user="3")
    await poll_log_now(entry)
    await async_wait_recording_done(hass)

    processor = EventProcessor(hass, (EVENT_LOG_ENTRY,), entity_ids=[LOG])
    rows = await recorder_mock.async_add_executor_job(
        processor.get_events, start, dt_util.utcnow() + timedelta(minutes=1)
    )
    time = dt_util.parse_datetime(get_state(hass, LOG).attributes["time"])
    assert time is not None
    written = dt_util.as_local(time)
    assert [(row["name"], row["message"]) for row in rows if row.get("message")] == [
        ("Alarmanlage Log", f"Ben 003 TB 1 aktiv ({written:%H:%M:%S})")
    ]
