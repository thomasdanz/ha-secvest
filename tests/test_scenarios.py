"""Scenarios beyond the reference installation (#148).

The fake panel mirrors the reference panel, where partition 1 has all
zones; these tests cover several partitions with a shared zone, a tampered
zone with a blocking fault, a German instance, reauthentication during a
setup retry, concurrent commands and an unload during a command.
"""

import asyncio
from typing import Any

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_PASSWORD,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.secvest.api.models import PartitionState
from custom_components.secvest.commands import CommandError, Failure, explain
from custom_components.secvest.const import (
    CONF_AUTH_FAILED,
    CONF_HIDE_MEMBERS,
    CONF_PARTITIONS,
    CONF_USER_CODE,
    CONF_ZONES,
    SUBENTRY_ZONE_GROUP,
)
from custom_components.secvest.coordinator import CommandOutcome

from .common import (
    CODE,
    ROUND,
    Setup,
    call_panel,
    coordinator_of,
    get_state,
    state_of,
)
from .fake_panel import SHARED_ZONE, FakePanel, Injection

SHARED = "binary_sensor.alarmanlage_room_6_l"
SHARED_OMIT = "switch.alarmanlage_room_6_l_omit"
ARMING_1 = "binary_sensor.alarmanlage_teilber_1_arming"
ARMING_2 = "binary_sensor.alarmanlage_teilber_2_arming"


def _fault(**changes: Any) -> dict[str, Any]:
    fault: dict[str, Any] = {
        "type": "1234",
        "id": "7",
        "ui-string": "Sabotage",
        "affects-partition": ["1"],
        "prevents-set": True,
        "prevents-reset": False,
        "is-rf-warning": False,
    }
    fault.update(changes)
    return fault


# several partitions with a shared zone


async def test_shared_zone_once(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A zone in two selected partitions is read and shown once."""
    fake_panel.split_partitions()
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    state = coordinator_of(entry).data
    assert len(state.zones) == len(fake_panel.zones)
    assert get_state(hass, SHARED).attributes["partitions"] == [1, 2]
    registry = er.async_get(hass)
    shared = [
        entity
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.unique_id == f"{entry.entry_id}_zone_{SHARED_ZONE}_open"
    ]
    assert len(shared) == 1
    # each zone list is read once per round
    assert fake_panel.stats.requests == [
        *ROUND,
        ("GET", "/system/partitions-2/zones/"),
    ]


@pytest.mark.parametrize(("selected", "through"), [([1, 2], 1), ([2, 1], 2)])
async def test_shared_zone_omitted_through_the_first_partition(
    hass: HomeAssistant,
    fake_panel: FakePanel,
    setup: Setup,
    selected: list[int],
    through: int,
) -> None:
    """Omitting goes through the first selected partition listing the zone."""
    fake_panel.split_partitions()
    await setup(**{CONF_PARTITIONS: selected})
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SHARED_OMIT}, blocking=True
    )
    assert fake_panel.zones[SHARED_ZONE].omitted
    assert (
        "PUT",
        f"/system/partitions-{through}/zones-{SHARED_ZONE}/",
    ) in fake_panel.stats.requests


async def test_shared_zone_without_rights_in_the_first_partition(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """No rights there means "no permission", as the README states."""
    fake_panel.split_partitions()
    await setup(**{CONF_PARTITIONS: [1, 2]})
    fake_panel.rights = {2}
    with pytest.raises(CommandError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": SHARED_OMIT}, blocking=True
        )
    assert err.value.translation_key == "omit_failed_no_permission"
    assert not fake_panel.zones[SHARED_ZONE].omitted


async def test_fault_on_both_arming_sensors(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """An open shared zone and a fault of both partitions block both."""
    fake_panel.split_partitions()
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    assert state_of(hass, ARMING_1) == state_of(hass, ARMING_2) == STATE_OFF

    fake_panel.open_zone(SHARED_ZONE)
    await coordinator_of(entry).async_refresh()
    for entity_id in (ARMING_1, ARMING_2):
        assert state_of(hass, entity_id) == STATE_ON
        assert get_state(hass, entity_id).attributes["blocking_zones"] == [SHARED_ZONE]

    fake_panel.close_zone(SHARED_ZONE)
    fake_panel.static_faults.append(_fault(**{"affects-partition": ["1", "2"]}))
    await coordinator_of(entry).async_refresh()
    for entity_id in (ARMING_1, ARMING_2):
        assert state_of(hass, entity_id) == STATE_ON
        assert get_state(hass, entity_id).attributes["blocking_faults"] == ["Sabotage"]


async def test_group_across_partitions(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """A zone group may combine zones of two partitions."""
    fake_panel.split_partitions()
    entry = await setup(**{CONF_PARTITIONS: [1, 2]})
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ZONE_GROUP), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_NAME: "Across",
            CONF_ZONES: ["208", "217"],
            CONF_DEVICE_CLASS: "window",
            CONF_HIDE_MEMBERS: False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    group = "binary_sensor.alarmanlage_across"
    assert state_of(hass, group) == STATE_OFF
    fake_panel.open_zone("217")
    await coordinator_of(entry).async_refresh()
    assert state_of(hass, group) == STATE_ON
    assert get_state(hass, group).attributes["open_zones"] == ["217"]


# a tampered zone with a blocking fault


async def test_tampered_zone_with_a_blocking_fault(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Arming sensor, explain() and zone problem agree on the fault."""
    entry = await setup(code=CODE)
    fake_panel.zones["209"].state = "tamper"
    fake_panel.static_faults.append(
        _fault(**{"ui-string": "Sabotage Z209", "affects-zone": "209"})
    )
    await coordinator_of(entry).async_refresh()
    attributes = get_state(hass, ARMING_1).attributes
    assert state_of(hass, ARMING_1) == STATE_ON
    assert attributes["blocking_zones"] == []
    assert attributes["blocking_faults"] == ["Sabotage Z209"]
    # the zone isn't open, so the fault is the likely reason
    outcome = CommandOutcome(False, None, coordinator_of(entry).data, False)
    assert explain(outcome, 1, PartitionState.SET) == Failure(
        "likely_faults", faults=("Sabotage Z209",)
    )
    assert state_of(hass, SHARED) == STATE_UNKNOWN
    problem = get_state(hass, f"{SHARED}_problem")
    assert problem.state == STATE_ON
    assert problem.attributes["faults"] == ["Sabotage Z209"]


# a German instance


async def test_german_instance(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Entity ids and device names in German, as documented."""
    hass.config.language = "de"
    entry = await setup()
    registry = er.async_get(hass)
    ids = {
        entity.unique_id.removeprefix(f"{entry.entry_id}_"): entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert ids["faults"] == "sensor.alarmanlage_storungen"
    assert ids["installer_lock"] == "binary_sensor.alarmanlage_errichtersperre"
    assert ids["partition_1_arming_blocked"] == (
        "binary_sensor.alarmanlage_teilber_1_aktivierung"
    )
    assert ids["partition_1_open_zones"] == "sensor.alarmanlage_teilber_1_offene_zonen"
    # zone entities keep the explicit installation_zone pattern
    assert ids["zone_209_open"] == SHARED
    zone = registry.async_get(SHARED)
    assert zone is not None
    assert zone.device_id is not None
    device = dr.async_get(hass).async_get(zone.device_id)
    assert isinstance(device, dr.DeviceEntry)
    assert device.model == "Funkzone"
    assert get_state(hass, SHARED).attributes["friendly_name"] == ("Funkzone Room 6 L")


# reauthentication during a setup retry


def _state(entry: MockConfigEntry) -> ConfigEntryState:
    # a function call, so that mypy doesn't narrow the attribute
    state: ConfigEntryState = entry.state
    return state


async def test_reauth_during_setup_retry(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unreachable at setup, then a 401 at the retry, then new credentials."""
    fake_panel.inject(Injection("GET", "/system/partitions/", "drop_before", times=2))
    entry = await setup()
    assert _state(entry) is ConfigEntryState.SETUP_RETRY

    fake_panel.password = "new"
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(entry) is ConfigEntryState.SETUP_ERROR
    assert entry.data[CONF_AUTH_FAILED] is True
    sent = len(fake_panel.stats.requests)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USER_CODE: entry.data[CONF_USER_CODE], CONF_PASSWORD: "new"},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert _state(entry) is ConfigEntryState.LOADED
    # one check of the new credentials, then the first round
    assert fake_panel.stats.requests[sent:] == [("GET", "/system/"), *ROUND]


# concurrent commands and an unload during a command


async def test_concurrent_commands(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Two entities' commands at once are serialised and both verified."""
    await setup(code=CODE)
    await asyncio.gather(
        call_panel(hass, "alarm_arm_away"),
        hass.services.async_call(
            "switch", "turn_on", {"entity_id": SHARED_OMIT}, blocking=True
        ),
    )
    assert fake_panel.partitions[1].state == "set"
    assert fake_panel.zones["209"].omitted
    assert state_of(hass, SHARED_OMIT) == STATE_ON
    assert fake_panel.stats.max_open_connections == 1
    # the fake panel fixture fails the test on any parallel request


async def test_unload_during_a_command(
    hass: HomeAssistant, fake_panel: FakePanel, setup: Setup
) -> None:
    """Unloading waits for a running command; nothing is sent afterwards."""
    entry = await setup(code=CODE)
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "slow", delay=0.5))
    command = asyncio.create_task(call_panel(hass, "alarm_arm_away"))
    # the fake panel records the request in its own thread, so no Event
    while ("PUT", "/system/partitions-1/") not in fake_panel.stats.requests:  # noqa: ASYNC110
        await asyncio.sleep(0.02)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await command
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert fake_panel.partitions[1].state == "set"
    sent = len(fake_panel.stats.requests)
    await asyncio.sleep(0.2)
    assert len(fake_panel.stats.requests) == sent
