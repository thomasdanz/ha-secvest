"""Commands to the panel: each verified by the real state afterwards.

Every failure of a partition command ends in one error type, raised to the
caller (UI, automation trace), and in one event, so automations can react.
The reason comes from the panel's answer and the state read afterwards,
never from panel texts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError

from .api.errors import (
    ArmingBlockedError,
    AuthenticationError,
    ConnectionLostError,
    InstallerLockedError,
    NotAllowedError,
    SecvestError,
)
from .api.models import PartitionState
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import CommandOutcome, PanelState, SecvestCoordinator

_LOGGER = logging.getLogger(__name__)

EVENT_ARMING_FAILED = f"{DOMAIN}_arming_failed"


@dataclass(frozen=True, slots=True)
class Failure:
    """Why a partition command failed; key is the translation key's suffix."""

    reason: str
    zones: tuple[str, ...] = ()
    faults: tuple[str, ...] = ()


class CommandError(HomeAssistantError):
    """A command didn't reach its target; the message says why."""


def _names(state: PanelState, zone_ids: tuple[str, ...] | list[str]) -> str:
    names = [
        state.zones[zone_id].name if zone_id in state.zones else zone_id
        for zone_id in zone_ids
    ]
    return ", ".join(names)


def explain(outcome: CommandOutcome, number: int, target: PartitionState) -> Failure:
    """Derive why the partition didn't reach the target state.

    Certain from the answer: 409 names the blocking faults, an empty 403
    means no permission. After a 200 without effect the reason is derived
    from the fresh state and marked as likely.
    """
    certain = _from_answer(outcome.error)
    if certain is not None:
        return certain
    if target in ARMED:
        state = outcome.state
        if open_zones := state.open_zones(number):
            return Failure("likely_open_zones", tuple(z.id for z in open_zones))
        if blocking := state.blocking_problems(number):
            faults = tuple(f.text or f"{f.type}/{f.id}" for f in blocking)
            return Failure("likely_faults", faults=faults)
    return Failure("unknown")


def _from_answer(error: SecvestError | None) -> Failure | None:
    """Return the reason the panel's error answer gives, if any."""
    if isinstance(error, ArmingBlockedError):
        if not error.faults:
            return Failure("refused")
        zones = tuple(f.zone_id for f in error.faults if f.zone_id)
        faults = tuple(
            f.text or f"{f.type}/{f.id}" for f in error.faults if not f.zone_id
        )
        return Failure("blocked", zones, faults)
    if isinstance(error, NotAllowedError):
        return Failure("no_permission")
    if error is not None and not isinstance(error, ConnectionLostError):
        return Failure("error")
    return None


@dataclass(frozen=True, slots=True)
class Request:
    """A requested partition state and the action it stands for.

    action is arm, disarm or acknowledge, or for a failed step before the
    requested one: switch (disarming first when switching between the armed
    modes) or acknowledge_first (acknowledging before disarming an alarm).
    """

    number: int
    target: PartitionState
    action: str


def raise_failure(
    coordinator: SecvestCoordinator,
    request: Request,
    failure: Failure,
    state: PanelState,
) -> None:
    """Fire the arming_failed event and raise the translated error."""
    number, target, action = request.number, request.target, request.action
    partition = state.partitions.get(number)
    name = partition.name if partition is not None else str(number)
    coordinator.hass.bus.async_fire(
        EVENT_ARMING_FAILED,
        {
            "entry_id": coordinator.config_entry.entry_id,
            "partition": number,
            "partition_name": name,
            "requested": str(target),
            "reason": failure.reason,
            # the step that failed: disarm first when switching modes
            "step": _STEPS.get(action, "command"),
            "zones": list(failure.zones),
            "faults": list(failure.faults),
        },
    )
    _LOGGER.info(
        "Partition %s: %s to %s failed (%s)", number, action, target, failure.reason
    )
    raise CommandError(
        translation_domain=DOMAIN,
        translation_key=f"{action}_failed_{failure.reason}",
        translation_placeholders={
            "partition": name,
            # the blocking zones by name, then other faults as displayed
            "items": ", ".join(
                part
                for part in (_names(state, failure.zones), ", ".join(failure.faults))
                if part
            ),
        },
    )


def _panel_error(err: SecvestError) -> CommandError:
    """Map the errors after which no verification is possible."""
    if isinstance(err, InstallerLockedError):
        return CommandError(
            translation_domain=DOMAIN, translation_key="installer_locked"
        )
    if isinstance(err, AuthenticationError):
        return CommandError(translation_domain=DOMAIN, translation_key="auth_failed")
    return CommandError(
        translation_domain=DOMAIN,
        translation_key="not_verified",
        translation_placeholders={"error": str(err)},
    )


ARMED = frozenset({PartitionState.SET, PartitionState.PARTSET})
IN_ALARM = frozenset(
    {
        PartitionState.SET_ALARM,
        PartitionState.PARTSET_ALARM,
        PartitionState.UNSET_ALARM,
    }
)
# the event's step for a failed step before the requested one
_STEPS = {"switch": "disarm_first", "acknowledge_first": "acknowledge_first"}


def _reaches(number: int, target: PartitionState) -> Callable[[PanelState], bool]:
    def reached(state: PanelState) -> bool:
        partition = state.partitions.get(number)
        if partition is None:
            return False
        if target == PartitionState.ACKNOWLEDGED:
            # acknowledged, or already reset or disarmed meanwhile
            return partition.state not in IN_ALARM
        return partition.state == target

    return reached


def _current(coordinator: SecvestCoordinator, number: int) -> PartitionState | str:
    partition = coordinator.data.partitions.get(number)
    return partition.state if partition is not None else ""


async def async_set_partition_state(
    coordinator: SecvestCoordinator, number: int, target: PartitionState
) -> None:
    """Arm, arm internally or disarm a partition, verified.

    The panel ignores a direct switch between the armed modes, so that
    disarms first; disarming during an alarm acknowledges first, since the
    official app never sends unset from an alarm state. Arming during an
    alarm isn't sent at all. A sequence holds the queue, and the entity
    keeps its previous state until it ends. Raises CommandError if the
    partition isn't in the target state afterwards, whatever the panel
    answered; a failed intermediate step stops the sequence and is named.
    """
    async with coordinator.client.hold(priority=True):
        current = _current(coordinator, number)
        if target in ARMED and (
            current in IN_ALARM or current == PartitionState.ACKNOWLEDGED
        ):
            raise CommandError(
                translation_domain=DOMAIN,
                translation_key="arm_during_alarm",
                translation_placeholders={"partition": _name(coordinator, number)},
            )
        steps = [(target, _action(target))]
        if target == PartitionState.UNSET and current in IN_ALARM:
            steps.insert(0, (PartitionState.ACKNOWLEDGED, "acknowledge_first"))
        elif current in ARMED and target in ARMED and current != target:
            steps.insert(0, (PartitionState.UNSET, "switch"))
        await _run(coordinator, number, target, steps)


async def async_acknowledge(coordinator: SecvestCoordinator, number: int) -> None:
    """Acknowledge the alarm of a partition, verified.

    Only while the partition is in an alarm state, like the official app.
    """
    async with coordinator.client.hold(priority=True):
        if _current(coordinator, number) not in IN_ALARM:
            raise CommandError(
                translation_domain=DOMAIN,
                translation_key="no_alarm",
                translation_placeholders={"partition": _name(coordinator, number)},
            )
        target = PartitionState.ACKNOWLEDGED
        await _run(coordinator, number, target, [(target, "acknowledge")])


async def _run(
    coordinator: SecvestCoordinator,
    number: int,
    target: PartitionState,
    steps: list[tuple[PartitionState, str]],
) -> None:
    """Send the steps one after another, each verified; stop at a failure."""
    client = coordinator.client
    for index, (step, action) in enumerate(steps):
        last = index == len(steps) - 1
        try:
            outcome = await coordinator.async_command(
                partial(client.set_partition_state, number, step),
                _reaches(number, step),
                publish=last,
            )
        except SecvestError as err:
            raise _panel_error(err) from err
        if not outcome.reached:
            if not last:
                # show the real state the sequence stopped in
                coordinator.async_set_updated_data(outcome.state)
            raise_failure(
                coordinator,
                Request(number, target, action),
                explain(outcome, number, step),
                outcome.state,
            )


def _name(coordinator: SecvestCoordinator, number: int) -> str:
    partition = coordinator.data.partitions.get(number)
    return partition.name if partition is not None else str(number)


def _action(target: PartitionState) -> str:
    return "disarm" if target == PartitionState.UNSET else "arm"


async def async_set_omitted(
    coordinator: SecvestCoordinator, zone_id: str, omitted: bool
) -> None:
    """Omit a zone or include it again, verified by the zone read afterwards.

    The panel answers an empty 403 both for a zone that can't be omitted and
    for a partition the user may not operate; the fresh omittable tells them
    apart.
    """
    client = coordinator.client
    action = "omit" if omitted else "include"
    async with client.hold(priority=True):
        state = coordinator.data
        # the zone is omitted through one of the selected partitions it is in
        number = next(
            (
                n
                for n in coordinator.selected_partitions
                if (p := state.partitions.get(n)) is not None and zone_id in p.zone_ids
            ),
            None,
        )
        name = state.zones[zone_id].name if zone_id in state.zones else zone_id
        if number is None:
            raise CommandError(
                translation_domain=DOMAIN,
                translation_key=f"{action}_failed_unknown",
                translation_placeholders={"zone": name},
            )

        def reached(fresh: PanelState) -> bool:
            zone = fresh.zones.get(zone_id)
            return zone is not None and zone.omitted == omitted

        try:
            outcome = await coordinator.async_command(
                partial(client.set_zone_omitted, number, zone_id, omitted), reached
            )
        except SecvestError as err:
            raise _panel_error(err) from err
    if outcome.reached:
        return
    zone = outcome.state.zones.get(zone_id)
    if isinstance(outcome.error, NotAllowedError):
        reason = (
            "not_omittable"
            if zone is not None and not zone.omittable
            else "no_permission"
        )
    elif outcome.error is not None and not isinstance(
        outcome.error, ConnectionLostError
    ):
        reason = "error"
    else:
        reason = "unknown"
    _LOGGER.info("Zone %s: %s failed (%s)", zone_id, action, reason)
    raise CommandError(
        translation_domain=DOMAIN,
        translation_key=f"{action}_failed_{reason}",
        translation_placeholders={"zone": name},
    )
