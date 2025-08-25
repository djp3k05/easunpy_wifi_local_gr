from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_COLLECTOR_UPDATED

_LOGGER = logging.getLogger(__name__)

# Pretty names that match what we exposed in sensors:
OUTPUT_SOURCE_OPTIONS = ["UtilitySolarBat", "SolarUtilityBat", "SolarBatUtility"]
CHARGER_SOURCE_OPTIONS = ["Solar first", "Solar + Utility", "Only solar charging permitted"]
INPUT_RANGE_OPTIONS = ["Appliance", "UPS"]
OUTPUT_MODE_OPTIONS = [
    "Single machine output",
    "Parallel output",
    "Phase 1 of 3 Phase output",
    "Phase 2 of 3 Phase output",
    "Phase 3 of 3 Phase output",
    "Phase 1 of 2 Phase output",
    "Phase 2 of 2 Phase output (120°)",
    "Phase 2 of 2 Phase output (180°)",
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    group = hass.data[DOMAIN][entry.entry_id]
    collector = group  # stored by sensor.py
    entities: list[SelectEntity] = [
        OutputSourcePrioritySelect(collector),
        ChargerSourcePrioritySelect(collector),
        InputVoltageRangeSelect(collector),
        OutputModeSelect(collector),
    ]
    async_add_entities(entities)
    _LOGGER.debug("Select entities added")


class _BaseSelect(SelectEntity):
    _attr_should_poll = False

    def __init__(self, collector):
        self.collector = collector
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            self.async_write_ha_state()

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_COLLECTOR_UPDATED, _updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None


class OutputSourcePrioritySelect(_BaseSelect):
    @property
    def name(self) -> str:
        return "Output Source Priority"

    @property
    def options(self) -> list[str]:
        return OUTPUT_SOURCE_OPTIONS

    @property
    def current_option(self) -> Optional[str]:
        status = getattr(self.collector, "last_status", None)
        return getattr(status, "output_source_priority", None) if status else None

    async def async_select_option(self, option: str) -> None:
        isolar = self.collector.isolar
        ok = await isolar.set_output_source_priority(option)
        _LOGGER.info("Set Output Source Priority -> %s", ok)


class ChargerSourcePrioritySelect(_BaseSelect):
    @property
    def name(self) -> str:
        return "Charger Source Priority"

    @property
    def options(self) -> list[str]:
        return CHARGER_SOURCE_OPTIONS

    @property
    def current_option(self) -> Optional[str]:
        status = getattr(self.collector, "last_status", None)
        return getattr(status, "charger_source_priority", None) if status else None

    async def async_select_option(self, option: str) -> None:
        isolar = self.collector.isolar
        ok = await isolar.set_charger_source_priority(option)
        _LOGGER.info("Set Charger Source Priority -> %s", ok)


class InputVoltageRangeSelect(_BaseSelect):
    @property
    def name(self) -> str:
        return "Input Voltage Range"

    @property
    def options(self) -> list[str]:
        return INPUT_RANGE_OPTIONS

    @property
    def current_option(self) -> Optional[str]:
        status = getattr(self.collector, "last_status", None)
        return getattr(status, "input_voltage_range", None) if status else None

    async def async_select_option(self, option: str) -> None:
        isolar = self.collector.isolar
        ok = await isolar.set_grid_working_range(option)
        _LOGGER.info("Set Input Voltage Range -> %s", ok)


class OutputModeSelect(_BaseSelect):
    @property
    def name(self) -> str:
        return "Output Mode (QPIRI)"

    @property
    def options(self) -> list[str]:
        return OUTPUT_MODE_OPTIONS

    @property
    def current_option(self) -> Optional[str]:
        status = getattr(self.collector, "last_status", None)
        return getattr(status, "output_mode_qpiri", None) if status else None

    async def async_select_option(self, option: str) -> None:
        isolar = self.collector.isolar
        ok = await isolar.set_output_mode(option)
        _LOGGER.info("Set Output Mode (QPIRI) -> %s", ok)
