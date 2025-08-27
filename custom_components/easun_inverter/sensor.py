# custom_components/easun_inverter/sensor.py

"""Support for Easun Inverter sensors."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
    UnitOfFrequency,
    UnitOfApparentPower,
    UnitOfEnergy,
    PERCENTAGE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

if TYPE_CHECKING:
    from .easunpy.async_isolar import AsyncISolar

_LOGGER = logging.getLogger(__name__)

# The DataCollector and EasunSensor classes are defined here,
# but instantiated and managed from __init__.py

class DataCollector:
    """Centralized data collector for Easun Inverter."""

    def __init__(self, isolar: "AsyncISolar"):
        self._isolar = isolar
        self._data: dict = {}
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._last_update_start: datetime | None = None
        self._entities: list[Entity] = []
        _LOGGER.info(f"DataCollector initialized with model: {self._isolar.model}")

    def register_entity(self, entity: Entity) -> None:
        self._entities.append(entity)
        _LOGGER.debug(f"Registered entity: {entity.name}")

    async def is_update_stuck(self) -> bool:
        if self._last_update_start is None:
            return False
        from datetime import datetime
        elapsed = (datetime.now() - self._last_update_start).total_seconds()
        return elapsed > 30

    def update_last_command_status(self, success: bool):
        from datetime import datetime
        status = "Success" if success else "Fail"
        timestamp = datetime.now().strftime('%H:%M:%S')
        
        if "system" not in self._data: self._data["system"] = {}
        self._data["system"]["last_command_response"] = f"{status} @ {timestamp}"
        
        for entity in self._entities:
            if hasattr(entity, '_key') and entity._key == 'last_command_response':
                if hasattr(entity, 'update_from_collector'):
                    entity.update_from_collector()
                if entity.hass:
                    entity.async_schedule_update_ha_state(True)
                break

    async def update_data(self) -> None:
        from datetime import datetime
        import contextlib
        import asyncio

        if self._lock.locked():
            _LOGGER.warning("Previous update still in progress, skipping")
            return

        await self._lock.acquire()
        self._last_update_start = datetime.now()
        try:
            update_task = asyncio.create_task(self._do_update())
            try:
                await asyncio.wait_for(update_task, timeout=30)
                for entity in self._entities:
                    if hasattr(entity, 'update_from_collector'):
                        if not (hasattr(entity, '_key') and entity._key == 'last_command_response'):
                             entity.update_from_collector()
                _LOGGER.debug("Updated all registered entities")
                self._consecutive_failures = 0
            except asyncio.TimeoutError:
                _LOGGER.error("Data update timed out, cancelling")
                update_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await update_task
            except Exception as e:
                _LOGGER.error(f"Error during data update: {e}")
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._max_consecutive_failures:
                    _LOGGER.critical(f"Max consecutive failures reached ({self._max_consecutive_failures})")
        finally:
            self._last_update_start = None
            self._lock.release()

    async def _do_update(self) -> None:
        battery, pv, grid, output, status = await self._isolar.get_all_data()
        self._data["battery"] = battery.__dict__ if battery else {}
        self._data["pv"] = pv.__dict__ if pv else {}
        self._data["grid"] = grid.__dict__ if grid else {}
        self._data["output"] = output.__dict__ if output else {}
        self._data["system"] = status.__dict__ if status else {}
        last_response = self._data.get("system", {}).get("last_command_response", "Idle")
        if "system" not in self._data: self._data["system"] = {}
        self._data["system"]["last_command_response"] = last_response

    def get_data(self, section: str, key: str):
        return self._data.get(section, {}).get(key)


class EasunSensor(SensorEntity):
    """Representation of an Easun Inverter sensor."""

    def __init__(
        self,
        data_collector: DataCollector,
        unique_id: str,
        name: str,
        unit: str | None,
        section: str,
        key: str,
        converter=None,
    ):
        self._collector = data_collector
        self._unique_id = unique_id
        self._attr_name = name
        self._unit = unit
        self._section = section
        self._key = key
        self._converter = converter
        self._state = None
        self._collector.register_entity(self)

    @property
    def unique_id(self) -> str:
        return f"easun_{self._unique_id}"

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self) -> str | None:
        return self._unit

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "easun_inverter")},
            name="Easun Inverter",
            manufacturer="Easun",
            model=self._collector._isolar.model,
        )

    def update_from_collector(self) -> None:
        value = self._collector.get_data(self._section, self._key)
        if self._converter and value is not None:
            value = self._converter(value)
        self._state = value
        if self.hass:
            self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Easun Inverter sensors from a config entry."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    frequency_converter = lambda v: (v / 100) if v is not None else None

    entities: list[SensorEntity] = [
        EasunSensor(coordinator, "last_command_response", "Last Command Response", None, "system", "last_command_response"),
        EasunSensor(coordinator, "battery_voltage", "Battery Voltage", UnitOfElectricPotential.VOLT, "battery", "voltage"),
        EasunSensor(coordinator, "battery_current", "Battery Current", UnitOfElectricCurrent.AMPERE, "battery", "current"),
        EasunSensor(coordinator, "battery_power", "Battery Power", UnitOfPower.WATT, "battery", "power"),
        EasunSensor(coordinator, "battery_soc", "Battery SoC", PERCENTAGE, "battery", "soc"),
        EasunSensor(coordinator, "battery_temperature", "Inverter Temperature", UnitOfTemperature.CELSIUS, "battery", "temperature"),
        EasunSensor(coordinator, "battery_charge_current", "Battery Charge Current", UnitOfElectricCurrent.AMPERE, "battery", "charge_current"),
        EasunSensor(coordinator, "battery_discharge_current", "Battery Discharge Current", UnitOfElectricCurrent.AMPERE, "battery", "discharge_current"),
        EasunSensor(coordinator, "battery_voltage_scc", "Battery Voltage (SCC)", UnitOfElectricPotential.VOLT, "battery", "voltage_scc"),
        EasunSensor(coordinator, "battery_voltage_offset_fans", "Battery Voltage Offset for Fans", UnitOfElectricPotential.VOLT, "battery", "voltage_offset_for_fans"),
        EasunSensor(coordinator, "battery_eeprom_version", "EEPROM Version", None, "battery", "eeprom_version"),
        EasunSensor(coordinator, "pv_total_power", "PV Total Power", UnitOfPower.WATT, "pv", "total_power"),
        EasunSensor(coordinator, "pv_charging_power", "PV Charging Power", UnitOfPower.WATT, "pv", "charging_power"),
        EasunSensor(coordinator, "pv_charging_current", "PV Charging Current", UnitOfElectricCurrent.AMPERE, "pv", "charging_current"),
        EasunSensor(coordinator, "pv1_voltage", "PV1 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv1_voltage"),
        EasunSensor(coordinator, "pv1_current", "PV1 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv1_current"),
        EasunSensor(coordinator, "pv1_power", "PV1 Power", UnitOfPower.WATT, "pv", "pv1_power"),
        EasunSensor(coordinator, "pv2_voltage", "PV2 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv2_voltage"),
        EasunSensor(coordinator, "pv2_current", "PV2 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv2_current"),
        EasunSensor(coordinator, "pv2_power", "PV2 Power", UnitOfPower.WATT, "pv", "pv2_power"),
        EasunSensor(coordinator, "pv_energy_today", "PV Generated Today", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_today"),
        EasunSensor(coordinator, "pv_energy_total", "PV Generated Total", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_total"),
        EasunSensor(coordinator, "grid_voltage", "Grid Voltage", UnitOfElectricPotential.VOLT, "grid", "voltage"),
        EasunSensor(coordinator, "grid_power", "Grid Power", UnitOfPower.WATT, "grid", "power"),
        EasunSensor(coordinator, "grid_frequency", "Grid Frequency", UnitOfFrequency.HERTZ, "grid", "frequency", frequency_converter),
        EasunSensor(coordinator, "output_voltage", "Output Voltage", UnitOfElectricPotential.VOLT, "output", "voltage"),
        EasunSensor(coordinator, "output_current", "Output Current", UnitOfElectricCurrent.AMPERE, "output", "current"),
        EasunSensor(coordinator, "output_power", "Output Power", UnitOfPower.WATT, "output", "power"),
        EasunSensor(coordinator, "output_apparent_power", "Output Apparent Power", UnitOfApparentPower.VOLT_AMPERE, "output", "apparent_power"),
        EasunSensor(coordinator, "output_load_percentage", "Output Load Percentage", PERCENTAGE, "output", "load_percentage"),
        EasunSensor(coordinator, "output_frequency", "Output Frequency", UnitOfFrequency.HERTZ, "output", "frequency", frequency_converter),
        EasunSensor(coordinator, "operating_mode", "Operating Mode", None, "system", "mode_name"),
        EasunSensor(coordinator, "inverter_time", "Inverter Time", None, "system", "inverter_time"),
        EasunSensor(coordinator, "bus_voltage", "Bus Voltage", UnitOfElectricPotential.VOLT, "system", "bus_voltage"),
        EasunSensor(coordinator, "device_status_flags", "Device Status Flags", None, "system", "device_status_flags"),
        EasunSensor(coordinator, "device_status_flags2", "Device Status 2 Flags", None, "system", "device_status_flags2"),
        EasunSensor(coordinator, "warnings", "Warnings", None, "system", "warnings"),
        EasunSensor(coordinator, "grid_rating_voltage", "Grid Rating Voltage", UnitOfElectricPotential.VOLT, "system", "grid_rating_voltage"),
        EasunSensor(coordinator, "grid_rating_current", "Grid Rating Current", UnitOfElectricCurrent.AMPERE, "system", "grid_rating_current"),
        EasunSensor(coordinator, "ac_output_rating_voltage", "AC Output Rating Voltage", UnitOfElectricPotential.VOLT, "system", "ac_output_rating_voltage"),
        EasunSensor(coordinator, "ac_output_rating_frequency", "AC Output Rating Frequency", UnitOfFrequency.HERTZ, "system", "ac_output_rating_frequency"),
        EasunSensor(coordinator, "ac_output_rating_current", "AC Output Rating Current", UnitOfElectricCurrent.AMPERE, "system", "ac_output_rating_current"),
        EasunSensor(coordinator, "ac_output_rating_apparent_power", "AC Output Rating Apparent Power", UnitOfApparentPower.VOLT_AMPERE, "system", "ac_output_rating_apparent_power"),
        EasunSensor(coordinator, "ac_output_rating_active_power", "AC Output Rating Active Power", UnitOfPower.WATT, "system", "ac_output_rating_active_power"),
        EasunSensor(coordinator, "battery_rating_voltage", "Battery Rating Voltage", UnitOfElectricPotential.VOLT, "system", "battery_rating_voltage"),
        EasunSensor(coordinator, "battery_recharge_voltage", "Battery Re-Charge Voltage", UnitOfElectricPotential.VOLT, "system", "battery_recharge_voltage"),
        EasunSensor(coordinator, "battery_undervoltage", "Battery Under Voltage", UnitOfElectricPotential.VOLT, "system", "battery_undervoltage"),
        EasunSensor(coordinator, "battery_bulk_voltage", "Battery Bulk Voltage", UnitOfElectricPotential.VOLT, "system", "battery_bulk_voltage"),
        EasunSensor(coordinator, "battery_float_voltage", "Battery Float Voltage", UnitOfElectricPotential.VOLT, "system", "battery_float_voltage"),
        EasunSensor(coordinator, "battery_type", "Battery Type", None, "system", "battery_type"),
        EasunSensor(coordinator, "max_ac_charging_current", "Max AC Charging Current", UnitOfElectricCurrent.AMPERE, "system", "max_ac_charging_current"),
        EasunSensor(coordinator, "max_charging_current", "Max Charging Current", UnitOfElectricCurrent.AMPERE, "system", "max_charging_current"),
        EasunSensor(coordinator, "input_voltage_range", "Input Voltage Range", None, "system", "input_voltage_range"),
        EasunSensor(coordinator, "output_source_priority", "Output Source Priority", None, "system", "output_source_priority"),
        EasunSensor(coordinator, "charger_source_priority", "Charger Source Priority", None, "system", "charger_source_priority"),
        EasunSensor(coordinator, "parallel_max_num", "Parallel Max Num", None, "system", "parallel_max_num"),
        EasunSensor(coordinator, "machine_type", "Machine Type", None, "system", "machine_type"),
        EasunSensor(coordinator, "topology", "Topology", None, "system", "topology"),
        EasunSensor(coordinator, "output_mode_qpiri", "Output Mode (QPIRI)", None, "system", "output_mode_qpiri"),
        EasunSensor(coordinator, "battery_redischarge_voltage", "Battery Re-Discharge Voltage", UnitOfElectricPotential.VOLT, "system", "battery_redischarge_voltage"),
        EasunSensor(coordinator, "pv_ok_condition", "PV OK Condition", None, "system", "pv_ok_condition"),
        EasunSensor(coordinator, "pv_power_balance", "PV Power Balance", None, "system", "pv_power_balance"),
        EasunSensor(coordinator, "max_charging_time_cv", "Max Charging Time at CV", None, "system", "max_charging_time_cv"),
        EasunSensor(coordinator, "max_discharging_current", "Max Discharging Current", UnitOfElectricCurrent.AMPERE, "system", "max_discharging_current"),
    ]
    add_entities(entities)
    _LOGGER.debug("Easun Inverter sensors added")