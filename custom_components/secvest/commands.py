"""Commands to the panel: each verified by the real state afterwards.

Every failure of a partition command ends in one error type, raised to the
caller (UI, automation trace), and in one event, so automations can react.
The reason comes from the panel's answer and the state read afterwards,
never from panel texts.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    if target != PartitionState.UNSET:
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


def raise_failure(
    coordinator: SecvestCoordinator,
    number: int,
    target: PartitionState,
    failure: Failure,
    state: PanelState,
) -> None:
    """Fire the arming_failed event and raise the translated error."""
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
            "zones": list(failure.zones),
            "faults": list(failure.faults),
        },
    )
    action = "disarm" if target == PartitionState.UNSET else "arm"
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


async def async_set_partition_state(
    coordinator: SecvestCoordinator, number: int, target: PartitionState
) -> None:
    """Arm, arm internally or disarm a partition, verified.

    Raises CommandError if the partition isn't in the target state
    afterwards, whatever the panel answered.
    """
    client = coordinator.client

    def reached(state: PanelState) -> bool:
        partition = state.partitions.get(number)
        return partition is not None and partition.state == target

    async with client.hold(priority=True):
        try:
            outcome = await coordinator.async_command(
                lambda: client.set_partition_state(number, target), reached
            )
        except SecvestError as err:
            raise _panel_error(err) from err
    if not outcome.reached:
        raise_failure(
            coordinator, number, target, explain(outcome, number, target), outcome.state
        )
