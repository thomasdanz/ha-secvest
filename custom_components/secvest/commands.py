"""Commands to the panel: each verified by the real state afterwards.

Every failure of a partition command ends in one error type, raised to the
caller (UI, automation trace), and in one event, so automations can react.
The reason comes from the panel's answer and the state read afterwards,
never from panel texts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
import logging
from typing import TYPE_CHECKING

from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError

from .api.errors import (
    ArmingBlockedError,
    AuthenticationError,
    CommunicationError,
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

    # set once the arming_failed event has been fired for this failure
    reported = False


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

    action is arm or disarm, or for a failed step before the requested one:
    switch (disarming first when switching between the armed modes) or
    acknowledge_first (acknowledging before disarming an alarm). user is
    the name of the code that was entered; context is the calling action's,
    so automations can tell where it came from (a user, an automation, or
    neither, e.g. HomeKit).
    """

    number: int
    target: PartitionState
    action: str
    user: str | None = None
    context: Context | None = None


def fire_arming_failed(
    coordinator: SecvestCoordinator,
    request: Request,
    failure: Failure,
    state: PanelState | None = None,
) -> None:
    """Fire the arming_failed event, for every failed arm or disarm command.

    Also for failures without a verified state (connection lost, installer
    lock, wrong code, ...): HomeKit Bridge shows no message, so an
    automation's notification is the only way to tell such a user.
    """
    state = state or coordinator.data
    number, target, action = request.number, request.target, request.action
    coordinator.hass.bus.async_fire(
        EVENT_ARMING_FAILED,
        {
            "entry_id": coordinator.config_entry.entry_id,
            "partition": number,
            "partition_name": state.partition_name(number),
            "requested": str(target),
            "reason": failure.reason,
            # the step that failed: disarm first when switching modes
            "step": _STEPS.get(action, "command"),
            "zones": list(failure.zones),
            "zone_names": [
                state.zones[zone].name if zone in state.zones else zone
                for zone in failure.zones
            ],
            "faults": list(failure.faults),
            "user": request.user,
        },
        context=request.context,
    )
    _LOGGER.info(
        "Partition %s: %s to %s failed (%s)", number, action, target, failure.reason
    )


def raise_failure(
    coordinator: SecvestCoordinator,
    request: Request,
    failure: Failure,
    state: PanelState,
) -> None:
    """Fire the arming_failed event and raise the translated error."""
    fire_arming_failed(coordinator, request, failure, state)
    error = CommandError(
        translation_domain=DOMAIN,
        translation_key=f"{request.action}_failed_{failure.reason}",
        translation_placeholders={
            "partition": state.partition_name(request.number),
            # the blocking zones by name, then other faults as displayed
            "items": ", ".join(
                part
                for part in (_names(state, failure.zones), ", ".join(failure.faults))
                if part
            ),
        },
    )
    error.reported = True
    raise error


def _refuse_while_locked(coordinator: SecvestCoordinator) -> None:
    """Refuse a command before sending anything if the lock is known.

    The panel refuses commands while the installer is logged in anyway; a
    lock the last round saw needs no request to find out. A lock that
    started since then is reported by the panel's answer the same way.
    """
    if coordinator.installer_locked:
        raise CommandError(
            translation_domain=DOMAIN, translation_key="installer_locked"
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


async def _read_current(
    coordinator: SecvestCoordinator, number: int
) -> PartitionState | str:
    """Read the partition's state right before deciding a sequence.

    The last round can be up to an interval old; what to send first depends
    on the real state. One request, within the caller's hold.
    """
    try:
        partitions = await coordinator.client.get_partitions()
    except CommunicationError as err:
        # nothing was sent yet, so the state is certainly unchanged
        raise CommandError(
            translation_domain=DOMAIN,
            translation_key="unreachable",
            translation_placeholders={"error": str(err)},
        ) from err
    except SecvestError as err:
        coordinator.note_panel_error(err)
        raise _panel_error(err) from err
    for partition in partitions:
        if partition.number == number:
            return partition.state
    return ""


async def async_set_partition_state(
    coordinator: SecvestCoordinator,
    number: int,
    target: PartitionState,
    *,
    user: str | None = None,
    context: Context | None = None,
) -> None:
    """Arm, arm internally or disarm a partition, verified.

    The panel ignores a direct switch between the armed modes, so that
    disarms first; disarming during an alarm acknowledges first, since the
    official app never sends unset from an alarm state. Arming during an
    alarm isn't sent at all. A sequence holds the queue, and the entity
    keeps its previous state until it ends. Raises CommandError if the
    partition isn't in the target state afterwards, whatever the panel
    answered; a failed intermediate step stops the sequence and is named.
    Every failure fires the arming_failed event once.
    """
    request = Request(number, target, _action(target), user, context)
    try:
        await _set_partition_state(coordinator, request)
    except CommandError as err:
        if not err.reported:
            # the reasons without a verified state are the error's key
            reason = err.translation_key or "error"
            fire_arming_failed(coordinator, request, Failure(reason))
            err.reported = True
        raise


async def _set_partition_state(
    coordinator: SecvestCoordinator, request: Request
) -> None:
    number, target = request.number, request.target
    _refuse_while_locked(coordinator)
    async with coordinator.client.hold(priority=True):
        current = await _read_current(coordinator, number)
        if target in ARMED and (
            current in IN_ALARM or current == PartitionState.ACKNOWLEDGED
        ):
            raise CommandError(
                translation_domain=DOMAIN,
                translation_key="arm_during_alarm",
                translation_placeholders={
                    "partition": coordinator.data.partition_name(number)
                },
            )
        steps = [(target, _action(target))]
        if target == PartitionState.UNSET and current in IN_ALARM:
            steps.insert(0, (PartitionState.ACKNOWLEDGED, "acknowledge_first"))
        elif current in ARMED and target in ARMED and current != target:
            steps.insert(0, (PartitionState.UNSET, "switch"))
        await _run(coordinator, request, steps)


async def _run(
    coordinator: SecvestCoordinator,
    request: Request,
    steps: list[tuple[PartitionState, str]],
) -> None:
    """Send the steps one after another, each verified; stop at a failure."""
    client, number = coordinator.client, request.number
    for index, (step, action) in enumerate(steps):
        last = index == len(steps) - 1
        try:
            outcome = await coordinator.async_command(
                partial(client.set_partition_state, number, step),
                _reaches(number, step),
                publish=last,
            )
        except SecvestError as err:
            error = _panel_error(err)
            # name the step the sequence broke off at
            reason = error.translation_key or "error"
            fire_arming_failed(
                coordinator, replace(request, action=action), Failure(reason)
            )
            error.reported = True
            raise error from err
        if not outcome.reached:
            if not last:
                # show the real state the sequence stopped in
                coordinator.async_set_updated_data(outcome.state)
            raise_failure(
                coordinator,
                replace(request, action=action),
                explain(outcome, number, step),
                outcome.state,
            )


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
    _refuse_while_locked(coordinator)
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
