# easun_inverter/select.py

"""Select entities to change inverter enumerated settings."""
from __future__ import annotations

import logging
from typing import Final, Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN
from .sensor import DataCollector

_LOGGER = logging.getLogger(__name__)

# Static option sets (labels are the same ones we show in sensors from QPIRI)
POP_OPTIONS: Final[list[str]] = ["UtilitySolarBat", "SolarUtilityBat", "SolarBatUtility"]
PCP_OPTIONS: Final[list[str]] = ["Solar first", "Solar + Utility", "Only solar charging"]
PGR_OPTIONS: Final[list[str]] = ["Appliance", "UPS"]
POPM_OPTIONS: Final[list[str]] = [
    "Single machine output",
    "Parallel output",
    "Phase 1 of 3 Phase output",
    "Phase 2 of 3 Phase output",
    "Phase 3 of 3 Phase output",
    "Phase 1 of 2 Phase output",
    "Phase 2 of 2 Phase output (120°)",
    "Phase 2 of 2 Phase output (180°)",
]


class _BaseSelect(SelectEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: DataCollector, name: str, key: str, options: list[str]):
        self._coordinator = coordinator
        self._attr_name = name
        self._attr_unique_id = f"easun_select_{key}"
        self._options = options
        self._key = key
        self._attr_current_option: Optional[str] = None
        self._coordinator.register_entity(self)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "easun_inverter")},
            name="Easun Inverter",
            manufacturer="Easun",
        )

    @property
    def options(self) -> list[str]:
        return self._options

    @property
    def current_option(self) -> Optional[str]:
        # Read the latest value directly from the coordinator's data store
        value = self._coordinator.get_data("system", self._key)
        if value in self._options:
            self._attr_current_option = value
        return self._attr_current_option

    async def _get_isolar(self):
        # Get isolar and coordinator directly
        return self._coordinator._isolar, self._coordinator

    def update_from_collector(self) -> None:
        """Called by the coordinator when data is updated."""
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        raise NotImplementedError


class OutputSourcePrioritySelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Output Source Priority", "output_source_priority", POP_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_output_source_priority(option)
        _LOGGER.info("Set Output Source Priority -> %s", ok)
        if ok:
            await coord.update_data()


class ChargerSourcePrioritySelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Charger Source Priority", "charger_source_priority", PCP_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_charger_source_priority(option)
        _LOGGER.info("Set Charger Source Priority -> %s", ok)
        if ok:
            await coord.update_data()


class GridWorkingRangeSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Input Voltage Range", "input_voltage_range", PGR_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_grid_working_range(option)
        _LOGGER.info("Set Grid Working Range -> %s", ok)
        if ok:
            await coord.update_data()


class OutputModeSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Output Mode (QPIRI)", "output_mode_qpiri", POPM_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        # Map friendly -> protocol code via the same mapping as parser uses
        mapping = {
            "Single machine output": "single",
            "Parallel output": "parallel",
            "Phase 1 of 3 Phase output": "p1_3ph",
            "Phase 2 of 3 Phase output": "p2_3ph",
            "Phase 3 of 3 Phase output": "p3_3ph",
            "Phase 1 of 2 Phase output": "p1_2ph",
            "Phase 2 of 2 Phase output (120°)": "p2_2ph_120",
            "Phase 2 of 2 Phase output (180°)": "p2_2ph_180",
        }
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_output_mode(mapping[option])
        _LOGGER.info("Set Output Mode -> %s", ok)
        if ok:
            await coord.update_data()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    # Get the coordinator for this config entry
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    
    entities: list[SelectEntity] = [
        OutputSourcePrioritySelect(coordinator),
        ChargerSourcePrioritySelect(coordinator),
        GridWorkingRangeSelect(coordinator),
        OutputModeSelect(coordinator),
    ]
    add_entities(entities)
    _LOGGER.debug("Select entities added")