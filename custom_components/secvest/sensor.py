"""Sensors on the panel device."""

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SecvestConfigEntry
from .api.models import PanelEvent
from .entity import SecvestEntity

# the entities only read the coordinator's state
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SecvestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of a panel."""
    async_add_entities([FaultsSensor(entry.runtime_data, "faults")])


def _fault(fault: PanelEvent) -> dict[str, Any]:
    return {
        "type": str(fault.type),
        "id": fault.id,
        "text": fault.text,
        "partitions": list(fault.partitions),
        "zone": fault.zone_id,
        "prevents_set": fault.prevents_set,
        "prevents_reset": fault.prevents_reset,
        "is_rf_warning": fault.is_rf_warning,
    }


def _line(fault: PanelEvent) -> str:
    # the panel's text is only displayed; faults are told apart by type and id
    return fault.text or f"Fault {fault.type}/{fault.id}"


class FaultsSensor(SecvestEntity, SensorEntity):
    """The number of current faults, with all of them as attributes.

    One place for all faults, including those of components the API doesn't
    list as objects (e.g. a repeater's battery); no entity per fault.
    """

    _attr_translation_key = "faults"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of current faults."""
        return len(self.coordinator.data.faults)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the faults and a readable summary, one line per fault."""
        faults = self.coordinator.data.faults
        return {
            "faults": [_fault(fault) for fault in faults],
            "summary": "\n".join(_line(fault) for fault in faults),
        }
