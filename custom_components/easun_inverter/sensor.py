"""Support for Easun Inverter sensors."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta

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
    # device classes intentionally not used to keep output simple
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from . import DOMAIN
from easunpy.async_isolar import AsyncISolar

_LOGGER = logging.getLogger(__name__)

DEFAULT_SCAN_INTERVAL = 30  # seconds


class DataCollector:
    """Centralized data collector for Easun Inverter."""

    def __init__(self, isolar: AsyncISolar):
        self._isolar = isolar
        self._data: dict = {}
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._last_update_start: datetime | None = None
        self._last_successful_update: datetime | None = None
        self._update_timeout = 30
        self._sensors: list[SensorEntity] = []
        _LOGGER.info(f"DataCollector initialized with model: {self._isolar.model}")

    def register_sensor(self, sensor: SensorEntity) -> None:
        self._sensors.append(sensor)
        _LOGGER.debug(f"Registered sensor: {sensor.name}")

    async def is_update_stuck(self) -> bool:
        if self._last_update_start is None:
            return False
        elapsed = (datetime.now() - self._last_update_start).total_seconds()
        return elapsed > self._update_timeout

    async def update_data(self) -> None:
        if self._lock.locked():
            _LOGGER.warning("Previous update still in progress, skipping")
            return

        await self._lock.acquire()
        try:
            update_task = asyncio.create_task(self._do_update())
            try:
                await asyncio.wait_for(update_task, timeout=self._update_timeout)
                for sensor in self._sensors:
                    sensor.update_from_collector()
                _LOGGER.debug("Updated all registered sensors")
                self._last_successful_update = datetime.now()
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
                    _LOGGER.critical(
                        f"Max consecutive failures reached ({self._max_consecutive_failures})"
                    )
        finally:
            self._lock.release()

    async def _do_update(self) -> None:
        battery, pv, grid, output, status = await self._isolar.get_all_data()
        self._data["battery"] = battery.__dict__ if battery else {}
        self._data["pv"] = pv.__dict__ if pv else {}
        self._data["grid"] = grid.__dict__ if grid else {}
        self._data["output"] = output.__dict__ if output else {}
        self._data["system"] = status.__dict__ if status else {}

    def get_data(self, section: str, key: str):
        return self._data.get(section, {}).get(key)


class EasunSensor(SensorEntity):
    """Representation of an Easun Inverter sensor."""
    _attr_should_poll = False


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
        self._collector.register_sensor(self)

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
            sw_version="1.0",
        )

    def update_from_collector(self) -> None:
        value = self._collector.get_data(self._section, self._key)
        if self._converter and value is not None:
            value = self._converter(value)
        self._state = value
        try:
            if getattr(self, 'hass', None):
                self.async_write_ha_state()
        except Exception:
            pass


class RegisterScanSensor(SensorEntity):
    def __init__(self, hass: HomeAssistant):
        self._attr_name = "Easun Register Scan"
        self._attr_unique_id = "easun_register_scan"
        self._state = None
        self._hass = hass

    @property
    def state(self):
        return self._state

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "easun_inverter")},
            name="Easun Inverter",
            manufacturer="Easun",
        )

    def update(self):
        scan = self._hass.data.get(DOMAIN, {}).get("register_scan", {})
        self._state = scan.get("timestamp", "No scan")


class DeviceScanSensor(SensorEntity):
    def __init__(self, hass: HomeAssistant):
        self._attr_name = "Easun Device Scan"
        self._attr_unique_id = "easun_device_scan"
        self._state = None
        self._hass = hass

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._hass.data.get(DOMAIN, {}).get("device_scan")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "easun_inverter")},
            name="Easun Inverter",
            manufacturer="Easun",
        )

    def update(self):
        scan = self._hass.data.get(DOMAIN, {}).get("device_scan", {})
        self._state = scan.get("timestamp", "No scan")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Easun Inverter sensors from a config entry."""
    entry_id = config_entry.entry_id
    inverter_ip = config_entry.data["inverter_ip"]
    local_ip = config_entry.data["local_ip"]
    model = config_entry.data["model"]

    # Read scan_interval from options, fallback to DEFAULT_SCAN_INTERVAL
    scan_interval = config_entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
    _LOGGER.debug(f"Setting up entry with scan_interval: {scan_interval}")

    # Initialize inverter client and data collector
    isolar = AsyncISolar(inverter_ip, local_ip, model)
    data_collector = DataCollector(isolar)

    # Ensure domain data structure exists
    hass.data.setdefault(DOMAIN, {})[entry_id] = {"coordinator": data_collector}

    # Helper: Hz fields are reported as int(Hz*100)
    frequency_converter = lambda v: (v / 100) if v is not None else None

    # -----------------------------
    # Build sensor entities
    # -----------------------------
    entities: list[SensorEntity] = [
        # Battery runtime
        EasunSensor(data_collector, "battery_voltage", "Battery Voltage", UnitOfElectricPotential.VOLT, "battery", "voltage"),
        EasunSensor(data_collector, "battery_current", "Battery Current", UnitOfElectricCurrent.AMPERE, "battery", "current"),
        EasunSensor(data_collector, "battery_power", "Battery Power", UnitOfPower.WATT, "battery", "power"),
        EasunSensor(data_collector, "battery_soc", "Battery SoC", PERCENTAGE, "battery", "soc"),
        EasunSensor(data_collector, "battery_temperature", "Inverter Temperature", UnitOfTemperature.CELSIUS, "battery", "temperature"),
        EasunSensor(data_collector, "battery_charge_current", "Battery Charge Current", UnitOfElectricCurrent.AMPERE, "battery", "charge_current"),
        EasunSensor(data_collector, "battery_discharge_current", "Battery Discharge Current", UnitOfElectricCurrent.AMPERE, "battery", "discharge_current"),
        EasunSensor(data_collector, "battery_voltage_scc", "Battery Voltage (SCC)", UnitOfElectricPotential.VOLT, "battery", "voltage_scc"),
        EasunSensor(data_collector, "battery_voltage_offset_fans", "Battery Voltage Offset for Fans", UnitOfElectricPotential.VOLT, "battery", "voltage_offset_for_fans"),
        EasunSensor(data_collector, "battery_eeprom_version", "EEPROM Version", None, "battery", "eeprom_version"),

        # PV runtime (note: PV Temperature removed)
        EasunSensor(data_collector, "pv_total_power", "PV Total Power", UnitOfPower.WATT, "pv", "total_power"),
        EasunSensor(data_collector, "pv_charging_power", "PV Charging Power", UnitOfPower.WATT, "pv", "charging_power"),
        EasunSensor(data_collector, "pv_charging_current", "PV Charging Current", UnitOfElectricCurrent.AMPERE, "pv", "charging_current"),
        EasunSensor(data_collector, "pv1_voltage", "PV1 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv1_voltage"),
        EasunSensor(data_collector, "pv1_current", "PV1 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv1_current"),
        EasunSensor(data_collector, "pv1_power", "PV1 Power", UnitOfPower.WATT, "pv", "pv1_power"),
        EasunSensor(data_collector, "pv2_voltage", "PV2 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv2_voltage"),
        EasunSensor(data_collector, "pv2_current", "PV2 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv2_current"),
        EasunSensor(data_collector, "pv2_power", "PV2 Power", UnitOfPower.WATT, "pv", "pv2_power"),
        EasunSensor(data_collector, "pv_energy_today", "PV Generated Today", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_today"),
        EasunSensor(data_collector, "pv_energy_total", "PV Generated Total", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_total"),

        # Grid/output runtime
        EasunSensor(data_collector, "grid_voltage", "Grid Voltage", UnitOfElectricPotential.VOLT, "grid", "voltage"),
        EasunSensor(data_collector, "grid_power", "Grid Power", UnitOfPower.WATT, "grid", "power"),
        EasunSensor(data_collector, "grid_frequency", "Grid Frequency", UnitOfFrequency.HERTZ, "grid", "frequency", frequency_converter),
        EasunSensor(data_collector, "output_voltage", "Output Voltage", UnitOfElectricPotential.VOLT, "output", "voltage"),
        EasunSensor(data_collector, "output_current", "Output Current", UnitOfElectricCurrent.AMPERE, "output", "current"),
        EasunSensor(data_collector, "output_power", "Output Power", UnitOfPower.WATT, "output", "power"),
        EasunSensor(data_collector, "output_apparent_power", "Output Apparent Power", UnitOfApparentPower.VOLT_AMPERE, "output", "apparent_power"),
        EasunSensor(data_collector, "output_load_percentage", "Output Load Percentage", PERCENTAGE, "output", "load_percentage"),
        EasunSensor(data_collector, "output_frequency", "Output Frequency", UnitOfFrequency.HERTZ, "output", "frequency", frequency_converter),

        # System/flags/ratings
        EasunSensor(data_collector, "operating_mode", "Operating Mode", None, "system", "mode_name"),
        EasunSensor(data_collector, "inverter_time", "Inverter Time", None, "system", "inverter_time"),
        EasunSensor(data_collector, "bus_voltage", "Bus Voltage", UnitOfElectricPotential.VOLT, "system", "bus_voltage"),
        EasunSensor(data_collector, "device_status_flags", "Device Status Flags", None, "system", "device_status_flags"),
        EasunSensor(data_collector, "device_status_flags2", "Device Status 2 Flags", None, "system", "device_status_flags2"),
        EasunSensor(data_collector, "warnings", "Warnings", None, "system", "warnings"),

        # QPIRI – ratings & settings
        EasunSensor(data_collector, "grid_rating_voltage", "Grid Rating Voltage", UnitOfElectricPotential.VOLT, "system", "grid_rating_voltage"),
        EasunSensor(data_collector, "grid_rating_current", "Grid Rating Current", UnitOfElectricCurrent.AMPERE, "system", "grid_rating_current"),
        EasunSensor(data_collector, "ac_output_rating_voltage", "AC Output Rating Voltage", UnitOfElectricPotential.VOLT, "system", "ac_output_rating_voltage"),
        EasunSensor(data_collector, "ac_output_rating_frequency", "AC Output Rating Frequency", UnitOfFrequency.HERTZ, "system", "ac_output_rating_frequency"),
        EasunSensor(data_collector, "ac_output_rating_current", "AC Output Rating Current", UnitOfElectricCurrent.AMPERE, "system", "ac_output_rating_current"),
        EasunSensor(data_collector, "ac_output_rating_apparent_power", "AC Output Rating Apparent Power", UnitOfApparentPower.VOLT_AMPERE, "system", "ac_output_rating_apparent_power"),
        EasunSensor(data_collector, "ac_output_rating_active_power", "AC Output Rating Active Power", UnitOfPower.WATT, "system", "ac_output_rating_active_power"),
        EasunSensor(data_collector, "battery_rating_voltage", "Battery Rating Voltage", UnitOfElectricPotential.VOLT, "system", "battery_rating_voltage"),
        EasunSensor(data_collector, "battery_recharge_voltage", "Battery Re-Charge Voltage", UnitOfElectricPotential.VOLT, "system", "battery_recharge_voltage"),
        EasunSensor(data_collector, "battery_undervoltage", "Battery Under Voltage", UnitOfElectricPotential.VOLT, "system", "battery_undervoltage"),
        EasunSensor(data_collector, "battery_bulk_voltage", "Battery Bulk Voltage", UnitOfElectricPotential.VOLT, "system", "battery_bulk_voltage"),
        EasunSensor(data_collector, "battery_float_voltage", "Battery Float Voltage", UnitOfElectricPotential.VOLT, "system", "battery_float_voltage"),
        EasunSensor(data_collector, "battery_type", "Battery Type", None, "system", "battery_type"),
        EasunSensor(data_collector, "max_ac_charging_current", "Max AC Charging Current", UnitOfElectricCurrent.AMPERE, "system", "max_ac_charging_current"),
        EasunSensor(data_collector, "max_charging_current", "Max Charging Current", UnitOfElectricCurrent.AMPERE, "system", "max_charging_current"),
        EasunSensor(data_collector, "input_voltage_range", "Input Voltage Range", None, "system", "input_voltage_range"),
        EasunSensor(data_collector, "output_source_priority", "Output Source Priority", None, "system", "output_source_priority"),
        EasunSensor(data_collector, "charger_source_priority", "Charger Source Priority", None, "system", "charger_source_priority"),
        EasunSensor(data_collector, "parallel_max_num", "Parallel Max Num", None, "system", "parallel_max_num"),
        EasunSensor(data_collector, "machine_type", "Machine Type", None, "system", "machine_type"),
        EasunSensor(data_collector, "topology", "Topology", None, "system", "topology"),
        EasunSensor(data_collector, "output_mode_qpiri", "Output Mode (QPIRI)", None, "system", "output_mode_qpiri"),
        EasunSensor(data_collector, "battery_redischarge_voltage", "Battery Re-Discharge Voltage", UnitOfElectricPotential.VOLT, "system", "battery_redischarge_voltage"),
        EasunSensor(data_collector, "pv_ok_condition", "PV OK Condition", None, "system", "pv_ok_condition"),
        EasunSensor(data_collector, "pv_power_balance", "PV Power Balance", None, "system", "pv_power_balance"),
        EasunSensor(data_collector, "max_charging_time_cv", "Max Charging Time at CV", None, "system", "max_charging_time_cv"),
        EasunSensor(data_collector, "max_discharging_current", "Max Discharging Current", UnitOfElectricCurrent.AMPERE, "system", "max_discharging_current"),

        # Diagnostics
        RegisterScanSensor(hass),
        DeviceScanSensor(hass),
    ]
    add_entities(entities, False)

    # Periodic updates and HA state write
    is_updating = False

    async def update_data_collector(now):
        nonlocal is_updating
        if is_updating:
            if not await data_collector.is_update_stuck():
                return
            _LOGGER.warning("Previous update stuck, forcing new cycle")
            is_updating = False

        _LOGGER.debug("Starting data collector update")
        is_updating = True
        data_collector._last_update_start = datetime.now()

        try:
            await data_collector.update_data()
            for sensor in entities:
                sensor.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(f"Error in update: {err}")
        finally:
            is_updating = False
            data_collector._last_update_start = None
            _LOGGER.debug("Data collector update finished")

    update_listener = async_track_time_interval(hass, update_data_collector, timedelta(seconds=scan_interval))
    config_entry.async_on_unload(update_listener)

    _LOGGER.debug("Easun Inverter sensors added")
