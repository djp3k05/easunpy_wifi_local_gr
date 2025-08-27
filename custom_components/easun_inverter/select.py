from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_COLLECTOR_UPDATED

_LOGGER = logging.getLogger(__name__)

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


def _get_status_value(hass: HomeAssistant, entry_id: str, *keys: str) -> Optional[str]:
    c = hass.data.get(DOMAIN, {}).get(entry_id)
    status = getattr(c, "last_status", None) if c else None
    if status is None:
        return None
    # support dataclass or dict
    for k in keys:
        if hasattr(status, k):
            v = getattr(status, k)
            if v is not None:
                return v
        if isinstance(status, dict) and k in status and status[k] is not None:
            return status[k]
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    entities: list[SelectEntity] = [
        OutputSourcePrioritySelect(hass, entry.entry_id),
        ChargerSourcePrioritySelect(hass, entry.entry_id),
        InputVoltageRangeSelect(hass, entry.entry_id),
        OutputModeSelect(hass, entry.entry_id),
    ]
    async_add_entities(entities)
    _LOGGER.debug("Select entities added")


class _BaseSelect(SelectEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry_id: str):
        self._hass = hass
        self._entry_id = entry_id
        self._unsub = None

    @property
    def _collector(self):
        return self._hass.data.get(DOMAIN, {}).get(self._entry_id)

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
        return _get_status_value(self.hass, self._entry_id, "output_source_priority")

    async def async_select_option(self, option: str) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot set Output Source Priority")
            return
        ok = await c.isolar.set_output_source_priority(option)
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
        return _get_status_value(self.hass, self._entry_id, "charger_source_priority")

    async def async_select_option(self, option: str) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot set Charger Source Priority")
            return
        ok = await c.isolar.set_charger_source_priority(option)
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
        return _get_status_value(self.hass, self._entry_id, "input_voltage_range")

    async def async_select_option(self, option: str) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot set Input Voltage Range")
            return
        ok = await c.isolar.set_grid_working_range(option)
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
        return _get_status_value(self.hass, self._entry_id, "output_mode_qpiri")

    async def async_select_option(self, option: str) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot set Output Mode")
            return
        ok = await c.isolar.set_output_mode(option)
        _LOGGER.info("Set Output Mode (QPIRI) -> %s", ok)
