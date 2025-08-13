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


class RegisterScanSensor(SensorEntity):
    """Sensor for register scan results."""
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
    """Sensor for device ID scan results."""
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

    scan_interval = config_entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
    _LOGGER.debug(f"Setting up entry with scan_interval: {scan_interval}")

    isolar = AsyncISolar(inverter_ip, local_ip, model)
    data_collector = DataCollector(isolar)

    hass.data.setdefault(DOMAIN, {})[entry_id] = {"coordinator": data_collector}

    frequency_converter = lambda v: (v / 100) if v is not None else None

    # Base entity set (shared by Modbus and ASCII)
    entities: list[SensorEntity] = [
        # Battery
        EasunSensor(data_collector, "battery_voltage", "Battery Voltage", UnitOfElectricPotential.VOLT, "battery", "voltage"),
        EasunSensor(data_collector, "battery_current", "Battery Current", UnitOfElectricCurrent.AMPERE, "battery", "current"),
        EasunSensor(data_collector, "battery_power", "Battery Power", UnitOfPower.WATT, "battery", "power"),
        EasunSensor(data_collector, "battery_soc", "Battery SoC", PERCENTAGE, "battery", "soc"),
        EasunSensor(data_collector, "battery_temperature", "Inverter Temperature", UnitOfTemperature.CELSIUS, "battery", "temperature"),
        # PV totals
        EasunSensor(data_collector, "pv_total_power", "PV Total Power", UnitOfPower.WATT, "pv", "total_power"),
        EasunSensor(data_collector, "pv_charging_power", "PV Charging Power", UnitOfPower.WATT, "pv", "charging_power"),
        EasunSensor(data_collector, "pv_charging_current", "PV Charging Current", UnitOfElectricCurrent.AMPERE, "pv", "charging_current"),
        # PV1 / PV2
        EasunSensor(data_collector, "pv1_voltage", "PV1 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv1_voltage"),
        EasunSensor(data_collector, "pv1_current", "PV1 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv1_current"),
        EasunSensor(data_collector, "pv1_power", "PV1 Power", UnitOfPower.WATT, "pv", "pv1_power"),
        EasunSensor(data_collector, "pv2_voltage", "PV2 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv2_voltage"),
        EasunSensor(data_collector, "pv2_current", "PV2 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv2_current"),
        EasunSensor(data_collector, "pv2_power", "PV2 Power", UnitOfPower.WATT, "pv", "pv2_power"),
        # Energy (only on Modbus models; will stay None on ASCII)
        EasunSensor(data_collector, "pv_energy_today", "PV Generated Today", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_today"),
        EasunSensor(data_collector, "pv_energy_total", "PV Generated Total", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_total"),
        # Grid & Output
        EasunSensor(data_collector, "grid_voltage", "Grid Voltage", UnitOfElectricPotential.VOLT, "grid", "voltage"),
        EasunSensor(data_collector, "grid_power", "Grid Power", UnitOfPower.WATT, "grid", "power"),
        EasunSensor(data_collector, "grid_frequency", "Grid Frequency", UnitOfFrequency.HERTZ, "grid", "frequency", frequency_converter),
        EasunSensor(data_collector, "output_voltage", "Output Voltage", UnitOfElectricPotential.VOLT, "output", "voltage"),
        EasunSensor(data_collector, "output_current", "Output Current", UnitOfElectricCurrent.AMPERE, "output", "current"),
        EasunSensor(data_collector, "output_power", "Output Power", UnitOfPower.WATT, "output", "power"),
        EasunSensor(data_collector, "output_apparent_power", "Output Apparent Power", UnitOfApparentPower.VOLT_AMPERE, "output", "apparent_power"),
        EasunSensor(data_collector, "output_load_percentage", "Output Load Percentage", PERCENTAGE, "output", "load_percentage"),
        EasunSensor(data_collector, "output_frequency", "Output Frequency", UnitOfFrequency.HERTZ, "output", "frequency", frequency_converter),
        # System status
        EasunSensor(data_collector, "operating_mode", "Operating Mode", None, "system", "mode_name"),
        EasunSensor(data_collector, "inverter_time", "Inverter Time", None, "system", "inverter_time"),
        RegisterScanSensor(hass),
        DeviceScanSensor(hass),
    ]

    # ASCII‑only sensors (VOLTRONIC_ASCII)
    if model == "VOLTRONIC_ASCII":
        entities.extend(
            [
                EasunSensor(data_collector, "battery_charge_current", "Battery Charge Current", UnitOfElectricCurrent.AMPERE, "battery", "charge_current"),
                EasunSensor(data_collector, "battery_discharge_current", "Battery Discharge Current", UnitOfElectricCurrent.AMPERE, "battery", "discharge_current"),
                EasunSensor(data_collector, "battery_voltage_scc", "Battery Voltage (SCC)", UnitOfElectricPotential.VOLT, "battery", "voltage_scc"),
                EasunSensor(data_collector, "device_status_flags", "Device Status Flags", None, "system", "device_status"),
                EasunSensor(data_collector, "device_status2_flags", "Device Status 2 Flags", None, "system", "device_status2"),
                EasunSensor(data_collector, "warnings", "Warnings", None, "system", "warnings"),
                EasunSensor(data_collector, "output_mode_qpiri", "Output Mode (QPIRI)", None, "system", "output_mode_qpiri"),
            ]
        )
    else:
        # Only non-ASCII models have a distinct PV temperature; ASCII duplicates heatsink temp.
        entities.insert(
            6,
            EasunSensor(
                data_collector, "pv_temperature", "PV Temperature",
                UnitOfTemperature.CELSIUS, "pv", "temperature"
            ),
        )

    add_entities(entities, False)

    # schedule periodic updates
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

    update_listener = async_track_time_interval(
        hass, update_data_collector, timedelta(seconds=scan_interval)
    )
    config_entry.async_on_unload(update_listener)

    _LOGGER.debug("Easun Inverter sensors added")
