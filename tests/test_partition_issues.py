"""Repair issues for selected partitions that are empty or missing."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest

from custom_components.secvest.const import (
    CONF_ADVANCED,
    CONF_PARTITIONS,
    CONF_SCAN_INTERVAL,
    CONF_USER_AGENT,
    DOMAIN,
)

from .common import ROUND, Setup, coordinator_of
from .fake_panel import FakePanel


def _issue(hass: HomeAssistant, entry_id: str, number: int) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"partition_{entry_id}_{number}")


async def test_empty_partition(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A selected partition without zones raises an issue, and isn't read."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    issue = _issue(hass, entry.entry_id, 2)
    assert issue is not None
    assert issue.translation_key == "empty_partition"
    assert issue.translation_placeholders == {
        "partition": "2 (Teilber. 2)",
        "name": "Alarmanlage",
    }
    assert _issue(hass, entry.entry_id, 1) is None
    # the empty partition's zone list isn't requested
    assert fake_panel.stats.requests == ROUND

    # zones added at the panel: the issue goes away
    fake_panel.partitions[2].zone_ids.append("209")
    await coordinator_of(entry).async_refresh()
    assert _issue(hass, entry.entry_id, 2) is None

    # empty again: back
    fake_panel.partitions[2].zone_ids.remove("209")
    await coordinator_of(entry).async_refresh()
    assert _issue(hass, entry.entry_id, 2) is not None


async def test_missing_partition(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A selected partition the panel doesn't report raises an issue once."""
    entry = await setup(**{CONF_PARTITIONS: [1, 9]})
    issue = _issue(hass, entry.entry_id, 9)
    assert issue is not None
    assert issue.translation_key == "missing_partition"
    assert issue.translation_placeholders == {"partition": "9", "name": "Alarmanlage"}
    await coordinator_of(entry).async_refresh()
    assert caplog.text.count("Selected partition 9: missing_partition") == 1
    assert "/system/partitions-9/zones/" not in {
        path for _, path in fake_panel.stats.requests
    }


async def test_issue_survives_a_reload(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """While the partition is still empty, a reload keeps the issue."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, entry.entry_id, 2) is not None


async def test_deselecting_removes_the_issue(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Deselecting the partition in the options removes the issue."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PARTITIONS: ["1"],
            CONF_SCAN_INTERVAL: 30,
            CONF_ADVANCED: {CONF_USER_AGENT: ""},
        },
    )
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.async_block_till_done()
    assert _issue(hass, entry.entry_id, 2) is None


async def test_issues_removed_with_the_entry(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Removing the entry removes its issues."""
    entry = await setup(**{CONF_PARTITIONS: [1, 2, 9]})
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert [i for d, i in ir.async_get(hass).issues if d == DOMAIN] == []


async def test_issue_of_an_older_version_removed(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An issue with the 0.1.x id doesn't outlive the update."""
    old = "missing_partition_{}_2"
    # the entry id is only known after setup, so create the old issue then
    entry = await setup()
    ir.async_create_issue(
        hass,
        DOMAIN,
        old.format(entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="missing_partition",
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert [i for d, i in ir.async_get(hass).issues if d == DOMAIN] == []
