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

# Static option sets
POP_OPTIONS: Final[list[str]] = ["UtilitySolarBat", "SolarUtilityBat", "SolarBatUtility"]
# MODIFIED: Updated PCP_OPTIONS list
PCP_OPTIONS: Final[list[str]] = [
    "Utility first",
    "Solar first",
    "Solar and utility simultaneously",
    "Solar only",
]
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
MAX_CHARGING_CURRENT_OPTIONS: Final[list[str]] = [str(i) for i in range(10, 121, 10)]
MAX_UTILITY_CHARGING_CURRENT_OPTIONS: Final[list[str]] = ['2'] + [str(i) for i in range(10, 121, 10)]

# Helper
def _fmt_one_decimal(value):
    if value is None:
        return None
    try:
        return f"{float(value):.1f}"
    except Exception:
        return None


class _BaseSelect(SelectEntity):
    _attr_should_poll = False

    def __init__(self, coordinator: DataCollector, name: str, key: str, options: list[str], decoder=None):
        self._coordinator = coordinator
        self._decode = decoder
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
        value = self._coordinator.get_data("system", self._key)
        label = None
        if getattr(self, '_decode', None):
            try:
                label = self._decode(value)
            except Exception:
                label = None
        if label is None:
            label = self._default_decode(value)
        if label in self._options:
            self._attr_current_option = label
        return self._attr_current_option

    async def _get_isolar(self):
        return self._coordinator._isolar, self._coordinator

    def _default_decode(self, value) -> Optional[str]:
        if value is None:
            return None
        s = str(value)
        try:
            f = float(value)
            if f.is_integer():
                s_int = str(int(f))
                if hasattr(self, '_options') and s_int in self._options:
                    return s_int
        except Exception:
            pass
        if hasattr(self, '_options'):
            for cand in (s, s.rstrip('0').rstrip('.')):
                if cand in self._options:
                    return cand
                if cand + 'A' in self._options:
                    return cand + 'A'
        return None

    def update_from_collector(self) -> None:
        """Called by the coordinator when data is updated."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        try:
            self.async_write_ha_state()
        except Exception:
            pass

    async def async_select_option(self, option: str) -> None:
        raise NotImplementedError


class OutputSourcePrioritySelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Output Source Priority", "output_source_priority", POP_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_output_source_priority(option)
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Output Source Priority -> %s", ok)
        if ok: await coord.update_data()


class ChargerSourcePrioritySelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Charger Source Priority", "charger_source_priority", PCP_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_charger_source_priority(option)
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Charger Source Priority -> %s", ok)
        if ok: await coord.update_data()


class GridWorkingRangeSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Input Voltage Range", "input_voltage_range", PGR_OPTIONS)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_grid_working_range(option)
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Grid Working Range -> %s", ok)
        if ok: await coord.update_data()


class OutputModeSelect(_BaseSelect):
    @staticmethod
    def _decode_output_mode(value) -> Optional[str]:
        if value is None:
            return None
        friendly = {
            "Single machine output",
            "Parallel output",
            "Phase 1 of 3 Phase output",
            "Phase 2 of 3 Phase output",
            "Phase 3 of 3 Phase output",
            "Phase 1 of 2 Phase output",
            "Phase 2 of 2 Phase output (120°)",
            "Phase 2 of 2 Phase output (180°)",
        }
        if str(value) in friendly:
            return str(value)
        
        code_map = {
            "00": "Single machine output",
            "01": "Parallel output",
            "02": "Phase 1 of 3 Phase output",
            "03": "Phase 2 of 3 Phase output",
            "04": "Phase 3 of 3 Phase output",
            "05": "Phase 1 of 2 Phase output",
            "06": "Phase 2 of 2 Phase output (120°)",
            "07": "Phase 2 of 2 Phase output (180°)",
            # Some firmwares report single-digit codes:
            "0": "Single machine output",
            "1": "Parallel output",
            "2": "Phase 1 of 3 Phase output",
            "3": "Phase 2 of 3 Phase output",
            "4": "Phase 3 of 3 Phase output",
            "5": "Phase 1 of 2 Phase output",
            "6": "Phase 2 of 2 Phase output (120°)",
            "7": "Phase 2 of 2 Phase output (180°)",
            # Friendly tokens we might store internally:
            "single": "Single machine output",
            "parallel": "Parallel output",
            "p1_3ph": "Phase 1 of 3 Phase output",
            "p2_3ph": "Phase 2 of 3 Phase output",
            "p3_3ph": "Phase 3 of 3 Phase output",
            "p1_2ph": "Phase 1 of 2 Phase output",
            "p2_2ph_120": "Phase 2 of 2 Phase output (120°)",
            "p2_2ph_180": "Phase 2 of 2 Phase output (180°)",
        }
        return code_map.get(str(value))

    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Output Mode (QPIRI)", "output_mode_qpiri", POPM_OPTIONS, decoder=OutputModeSelect._decode_output_mode)

    async def async_select_option(self, option: str) -> None:
        mapping = {
            "Single machine output": "single", "Parallel output": "parallel",
            "Phase 1 of 3 Phase output": "p1_3ph", "Phase 2 of 3 Phase output": "p2_3ph",
            "Phase 3 of 3 Phase output": "p3_3ph", "Phase 1 of 2 Phase output": "p1_2ph",
            "Phase 2 of 2 Phase output (120°)": "p2_2ph_120", "Phase 2 of 2 Phase output (180°)": "p2_2ph_180",
        }
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_output_mode(mapping[option])
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Output Mode -> %s", ok)
        if ok: await coord.update_data()

# NEW ENTITIES (existing)
class MaxChargingCurrentSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Max Charging Current", "max_charging_current", MAX_CHARGING_CURRENT_OPTIONS, decoder=lambda v: (str(int(float(v))) if v is not None else None))

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_max_charging_current(int(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Max Charging Current -> %s", ok)
        if ok: await coord.update_data()

class MaxUtilityChargingCurrentSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Max Utility Charging Current", "max_ac_charging_current", MAX_UTILITY_CHARGING_CURRENT_OPTIONS, decoder=lambda v: (str(int(float(v))) if v is not None else None))

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_max_utility_charging_current(int(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Max Utility Charging Current -> %s", ok)
        if ok: await coord.update_data()

# ---- NEW: voltage dropdown selects to replace sliders ----
BULK_FLOAT_OPTIONS = [f"{i/10:.1f}" for i in range(480, 641)]  # 48.0 .. 64.0
CUTOFF_OPTIONS = ["0.0"] + [f"{i/10:.1f}" for i in range(401, 541)]  # 0.0 + 40.1 .. 54.0
RECHARGE_OPTIONS = ["0.0"] + [f"{i:.1f}" for i in range(44, 52)]     # 0.0 + 44.0 .. 51.0 (step 1.0)
REDISCHARGE_OPTIONS = ["0.0"] + [f"{i:.1f}" for i in range(48, 65)]  # 0.0 + 48.0 .. 64.0 (step 1.0)

class BatteryBulkVoltageSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Battery Bulk/CV Voltage", "battery_bulk_voltage", BULK_FLOAT_OPTIONS, decoder=_fmt_one_decimal)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_battery_bulk_voltage(float(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Battery Bulk/CV Voltage -> %s", ok)
        if ok: await coord.update_data()

class BatteryCutoffVoltageSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Battery Cut-Off Voltage", "battery_undervoltage", CUTOFF_OPTIONS, decoder=_fmt_one_decimal)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_battery_cutoff_voltage(float(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Battery Cut-Off Voltage -> %s", ok)
        if ok: await coord.update_data()

class BatteryFloatVoltageSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Battery Float Voltage", "battery_float_voltage", BULK_FLOAT_OPTIONS, decoder=_fmt_one_decimal)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_battery_float_voltage(float(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Battery Float Voltage -> %s", ok)
        if ok: await coord.update_data()

class BatteryRechargeVoltageSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Battery Re-Charge Voltage", "battery_recharge_voltage", RECHARGE_OPTIONS, decoder=_fmt_one_decimal)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_battery_recharge_voltage(float(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Battery Re-Charge Voltage -> %s", ok)
        if ok: await coord.update_data()

class BatteryRedischargeVoltageSelect(_BaseSelect):
    def __init__(self, coordinator: DataCollector):
        super().__init__(coordinator, "Battery Re-Discharge Voltage", "battery_redischarge_voltage", REDISCHARGE_OPTIONS, decoder=_fmt_one_decimal)

    async def async_select_option(self, option: str) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar: return
        ok = await isolar.set_battery_redischarge_voltage(float(option))
        coord.update_last_command_status(ok)
        _LOGGER.info("Set Battery Re-Discharge Voltage -> %s", ok)
        if ok: await coord.update_data()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities: list[SelectEntity] = [
        OutputSourcePrioritySelect(coordinator),
        ChargerSourcePrioritySelect(coordinator),
        GridWorkingRangeSelect(coordinator),
        OutputModeSelect(coordinator),
        MaxChargingCurrentSelect(coordinator),
        MaxUtilityChargingCurrentSelect(coordinator),
        # New voltage dropdowns
        BatteryBulkVoltageSelect(coordinator),
        BatteryCutoffVoltageSelect(coordinator),
        BatteryFloatVoltageSelect(coordinator),
        BatteryRechargeVoltageSelect(coordinator),
        BatteryRedischargeVoltageSelect(coordinator),
    ]
    add_entities(entities)
    _LOGGER.debug("Select entities added")
