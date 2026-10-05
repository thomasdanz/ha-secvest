"""Form parts shared by the config flow, the options flow and the zone group flow."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .api.models import Partition, Zone
from .const import CONF_EXCLUDED_ZONES, ZONE_DEVICE_CLASSES


def partition_selector(partitions: Iterable[Partition]) -> SelectSelector:
    """Offer partitions for selection, labelled with number and name."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=str(p.number), label=f"{p.number}: {p.name}")
                for p in partitions
            ],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


def zone_labels(
    partitions: Mapping[int, Partition],
    zones: Mapping[str, Zone],
    selected: Iterable[int],
) -> list[tuple[str, str]]:
    """Return (zone id, label) for the zones of the selected partitions.

    The labels are the field names of the zones step, since the zones are
    the panel's and have no translations.
    """
    zone_ids = {
        zone_id
        for number in selected
        if (partition := partitions.get(number)) is not None
        for zone_id in partition.zone_ids
    }
    labels = []
    for zone_id in sorted(zone_ids, key=lambda value: (len(value), value)):
        # zones not read yet (a newly selected partition in the options) are
        # only known by their id
        zone = zones.get(zone_id)
        name = zone.name if zone is not None else "Zone"
        labels.append((zone_id, f"{name} ({zone_id})"))
    return labels


def zone_schema(
    zones: list[tuple[str, str]],
    classes: Mapping[str, str],
    excluded: Iterable[str],
) -> vol.Schema:
    """Build the zones step: excluded zones and a device class per zone."""
    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_EXCLUDED_ZONES,
            default=[zone_id for zone_id in excluded if zone_id in dict(zones)],
        ): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=zone_id, label=label)
                    for zone_id, label in zones
                ],
                multiple=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
    }
    device_classes = SelectSelector(
        SelectSelectorConfig(
            options=["none", *ZONE_DEVICE_CLASSES],
            translation_key="device_class",
            mode=SelectSelectorMode.DROPDOWN,
        )
    )
    for zone_id, label in zones:
        default = classes.get(zone_id, "none")
        fields[vol.Required(label, default=default)] = device_classes
    return vol.Schema(fields)


def zone_options(
    zones: list[tuple[str, str]],
    user_input: Mapping[str, Any],
    previous: Mapping[str, str],
) -> tuple[list[str], dict[str, str]]:
    """Return the excluded zones and the device classes from the zones step.

    Classes of zones that aren't shown, e.g. of a partition deselected for
    now, are kept.
    """
    shown = {zone_id for zone_id, _ in zones}
    excluded = [
        zone_id
        for zone_id, _ in zones
        if zone_id in user_input.get(CONF_EXCLUDED_ZONES, [])
    ]
    classes = {
        zone_id: device_class
        for zone_id, device_class in previous.items()
        if zone_id not in shown
    }
    for zone_id, label in zones:
        if (device_class := user_input.get(label, "none")) != "none":
            classes[zone_id] = device_class
    return excluded, classes
