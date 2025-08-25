from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from easunpy.async_isolar import AsyncISolar
from easunpy.models import BatteryData, GridData, OutputData, PVData, SystemStatus

from .const import (
    DOMAIN,
    CONF_INVERTER_IP,
    CONF_LOCAL_IP,
    CONF_MODEL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    SIGNAL_COLLECTOR_UPDATED,
)

_LOGGER = logging.getLogger(__name__)


class DataCollector:
    """Owns the AsyncISolar client and caches the last reading for other platforms."""

    def __init__(self, hass: HomeAssistant, inverter_ip: str, local_ip: str, model: str, scan_interval: int) -> None:
        self.hass = hass
        self.isolar = AsyncISolar(inverter_ip, local_ip, model=model)
        self.scan_interval = max(2, int(scan_interval or DEFAULT_SCAN_INTERVAL))

        self.last_battery: Optional[BatteryData] = None
        self.last_pv: Optional[PVData] = None
        self.last_grid: Optional[GridData] = None
        self.last_output: Optional[OutputData] = None
        self.last_status: Optional[SystemStatus] = None

        self._task_unsub = None
        self._updating = False

        _LOGGER.info("DataCollector initialized with model: %s", model)

    async def start(self) -> None:
        if self._task_unsub is None:
            self._task_unsub = async_track_time_interval(
                self.hass, self._scheduled_update, timedelta(seconds=self.scan_interval)
            )
        # Also trigger an immediate poll for initial values
        await self.async_poll_once()

    async def stop(self) -> None:
        if self._task_unsub:
            self._task_unsub()
            self._task_unsub = None

    async def async_poll_once(self) -> None:
        if self._updating:
            _LOGGER.debug("Previous update still in progress, skipping")
            return
        self._updating = True
        _LOGGER.debug("Starting data collector update")
        try:
            batt, pv, grid, out, status = await self.isolar.get_all_data()
            if any(x is not None for x in (batt, pv, grid, out, status)):
                self.last_battery = batt or self.last_battery
                self.last_pv = pv or self.last_pv
                self.last_grid = grid or self.last_grid
                self.last_output = out or self.last_output
                self.last_status = status or self.last_status
                async_dispatcher_send(self.hass, SIGNAL_COLLECTOR_UPDATED)
                _LOGGER.debug("Updated all registered sensors")
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Error during data update: %s", getattr(exc, "args", [""])[0])
        finally:
            _LOGGER.debug("Data collector update finished")
            self._updating = False

    async def _scheduled_update(self, _now) -> None:
        await self.async_poll_once()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    inverter_ip = entry.data[CONF_INVERTER_IP]
    local_ip = entry.data[CONF_LOCAL_IP]
    model = entry.data.get(CONF_MODEL, "VOLTRONIC_ASCII")

    # Make one collector per entry and share it with other platforms
    group = hass.data.setdefault(DOMAIN, {})
    collector: DataCollector = group.get(entry.entry_id)
    if collector is None:
        collector = DataCollector(hass, inverter_ip, local_ip, model, scan_interval)
        group[entry.entry_id] = collector
        await collector.start()
    _LOGGER.debug("Setting up entry with scan_interval: %s", scan_interval)

    entities: list[SensorEntity] = []

    def add(name: str, icon: str, unit: Optional[str], value_fn):
        entities.append(GenericSensor(collector, name, icon, unit, value_fn))

    # Battery / PV / Grid / Output / SystemStatus mappings
    add("Battery Voltage", "mdi:car-battery", "V", lambda c: getattr(c.last_battery, "voltage", None))
    add("Battery Current", "mdi:current-dc", "A", lambda c: getattr(c.last_battery, "current", None))
    add("Battery Power", "mdi:flash", "W", lambda c: getattr(c.last_battery, "power", None))
    add("Battery SoC", "mdi:battery-high", "%", lambda c: getattr(c.last_battery, "soc", None))
    add("Inverter Temperature", "mdi:thermometer", "°C", lambda c: getattr(c.last_battery, "temperature", None))
    add("Battery Charge Current", "mdi:current-dc", "A", lambda c: getattr(c.last_battery, "charge_current", None))
    add("Battery Discharge Current", "mdi:current-dc", "A", lambda c: getattr(c.last_battery, "discharge_current", None))
    add("Battery Voltage (SCC)", "mdi:car-battery", "V", lambda c: getattr(c.last_battery, "voltage_scc", None))
    add("Battery Voltage Offset for Fans", "mdi:fan", None, lambda c: getattr(c.last_battery, "voltage_offset_for_fans", None))
    add("EEPROM Version", "mdi:chip", None, lambda c: getattr(c.last_battery, "eeprom_version", None))

    add("PV Total Power", "mdi:solar-power", "W", lambda c: getattr(c.last_pv, "total_power", None))
    add("PV Charging Power", "mdi:solar-power", "W", lambda c: getattr(c.last_pv, "charging_power", None))
    add("PV Charging Current", "mdi:current-dc", "A", lambda c: getattr(c.last_pv, "charging_current", None))
    add("PV1 Voltage", "mdi:solar-panel", "V", lambda c: getattr(c.last_pv, "pv1_voltage", None))
    add("PV1 Current", "mdi:current-dc", "A", lambda c: getattr(c.last_pv, "pv1_current", None))
    add("PV1 Power", "mdi:solar-power", "W", lambda c: getattr(c.last_pv, "pv1_power", None))
    add("PV2 Voltage", "mdi:solar-panel", "V", lambda c: getattr(c.last_pv, "pv2_voltage", None))
    add("PV2 Current", "mdi:current-dc", "A", lambda c: getattr(c.last_pv, "pv2_current", None))
    add("PV2 Power", "mdi:solar-power", "W", lambda c: getattr(c.last_pv, "pv2_power", None))
    add("PV Generated Today", "mdi:counter", None, lambda c: getattr(c.last_pv, "pv_generated_today", None))
    add("PV Generated Total", "mdi:counter", None, lambda c: getattr(c.last_pv, "pv_generated_total", None))

    add("Grid Voltage", "mdi:transmission-tower", "V", lambda c: getattr(c.last_grid, "voltage", None))
    add("Grid Power", "mdi:flash", "W", lambda c: getattr(c.last_grid, "power", None))
    add("Grid Frequency", "mdi:sine-wave", "Hz", lambda c: getattr(c.last_grid, "frequency", None))

    add("Output Voltage", "mdi:power-socket-eu", "V", lambda c: getattr(c.last_output, "voltage", None))
    add("Output Current", "mdi:current-ac", "A", lambda c: getattr(c.last_output, "current", None))
    add("Output Power", "mdi:flash", "W", lambda c: getattr(c.last_output, "power", None))
    add("Output Apparent Power", "mdi:flash", "VA", lambda c: getattr(c.last_output, "apparent_power", None))
    add("Output Load Percentage", "mdi:percent", "%", lambda c: getattr(c.last_output, "load_percentage", None))
    add("Output Frequency", "mdi:sine-wave", "Hz", lambda c: getattr(c.last_output, "frequency", None))

    add("Operating Mode", "mdi:power-settings", None, lambda c: getattr(c.last_status, "mode_name", None))
    add("Inverter Time", "mdi:clock-outline", None, lambda c: getattr(c.last_status, "inverter_time", None))
    add("Bus Voltage", "mdi:resistor", "V", lambda c: getattr(c.last_status, "bus_voltage", None))
    add("Device Status Flags", "mdi:flag-outline", None, lambda c: getattr(c.last_status, "device_status_flags", None))
    add("Device Status 2 Flags", "mdi:flag-outline", None, lambda c: getattr(c.last_status, "device_status_flags2", None))
    add("Warnings", "mdi:alert-outline", None, lambda c: getattr(c.last_status, "warnings", None))

    # Ratings / settings (read from QPIRI so UI controls can show current values)
    add("Grid Rating Voltage", "mdi:alpha-g-circle", "V", lambda c: getattr(c.last_status, "grid_rating_voltage", None))
    add("Grid Rating Current", "mdi:alpha-g-circle", "A", lambda c: getattr(c.last_status, "grid_rating_current", None))
    add("AC Output Rating Voltage", "mdi:alpha-a-circle", "V", lambda c: getattr(c.last_status, "ac_output_rating_voltage", None))
    add("AC Output Rating Frequency", "mdi:alpha-a-circle", "Hz", lambda c: getattr(c.last_status, "ac_output_rating_frequency", None))
    add("AC Output Rating Current", "mdi:alpha-a-circle", "A", lambda c: getattr(c.last_status, "ac_output_rating_current", None))
    add("AC Output Rating Apparent Power", "mdi:alpha-a-circle", "VA", lambda c: getattr(c.last_status, "ac_output_rating_apparent_power", None))
    add("AC Output Rating Active Power", "mdi:alpha-a-circle", "W", lambda c: getattr(c.last_status, "ac_output_rating_active_power", None))
    add("Battery Rating Voltage", "mdi:alpha-b-circle", "V", lambda c: getattr(c.last_status, "battery_rating_voltage", None))
    add("Battery Re-Charge Voltage", "mdi:alpha-b-circle", "V", lambda c: getattr(c.last_status, "battery_recharge_voltage", None))
    add("Battery Under Voltage", "mdi:alpha-b-circle", "V", lambda c: getattr(c.last_status, "battery_undervoltage", None))
    add("Battery Bulk Voltage", "mdi:alpha-b-circle", "V", lambda c: getattr(c.last_status, "battery_bulk_voltage", None))
    add("Battery Float Voltage", "mdi:alpha-b-circle", "V", lambda c: getattr(c.last_status, "battery_float_voltage", None))
    add("Battery Type", "mdi:car-battery", None, lambda c: getattr(c.last_status, "battery_type", None))
    add("Max AC Charging Current", "mdi:alpha-m-circle", "A", lambda c: getattr(c.last_status, "max_ac_charging_current", None))
    add("Max Charging Current", "mdi:alpha-m-circle", "A", lambda c: getattr(c.last_status, "max_charging_current", None))
    add("Input Voltage Range", "mdi:alpha-i-circle", None, lambda c: getattr(c.last_status, "input_voltage_range", None))
    add("Output Source Priority", "mdi:alpha-o-circle", None, lambda c: getattr(c.last_status, "output_source_priority", None))
    add("Charger Source Priority", "mdi:alpha-c-circle", None, lambda c: getattr(c.last_status, "charger_source_priority", None))
    add("Parallel Max Num", "mdi:alpha-p-circle", None, lambda c: getattr(c.last_status, "parallel_max_num", None))
    add("Machine Type", "mdi:cog", None, lambda c: getattr(c.last_status, "machine_type", None))
    add("Topology", "mdi:vector-triangle", None, lambda c: getattr(c.last_status, "topology", None))
    add("Output Mode (QPIRI)", "mdi:alpha-o-circle", None, lambda c: getattr(c.last_status, "output_mode_qpiri", None))
    add("Battery Re-Discharge Voltage", "mdi:alpha-b-circle", "V", lambda c: getattr(c.last_status, "battery_redischarge_voltage", None))
    add("PV OK Condition", "mdi:solar-power", None, lambda c: getattr(c.last_status, "pv_ok_condition", None))
    add("PV Power Balance", "mdi:solar-power", None, lambda c: getattr(c.last_status, "pv_power_balance", None))
    add("Max Charging Time at CV", "mdi:timer", "min", lambda c: getattr(c.last_status, "max_charging_time_cv", None))
    add("Max Discharging Current", "mdi:current-dc", "A", lambda c: getattr(c.last_status, "max_discharging_current", None))

    async_add_entities(entities)
    # First immediate refresh already triggered by collector.start()


class GenericSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, collector: DataCollector, name: str, icon: str, unit: str | None, value_fn):
        self._collector = collector
        self._name = name
        self._icon = icon
        self._unit = unit
        self._value_fn = value_fn
        self._unsub = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def icon(self) -> str | None:
        return self._icon

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._unit

    @property
    def native_value(self):
        try:
            return self._value_fn(self._collector)
        except Exception:  # noqa: BLE001
            return None

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            self.async_write_ha_state()

        self._unsub = self.hass.helpers.dispatcher.async_dispatcher_connect(
            SIGNAL_COLLECTOR_UPDATED, _updated
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
