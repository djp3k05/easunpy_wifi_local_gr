# sensor.py full code
"""Support for Easun Inverter sensors."""
from datetime import datetime, timedelta
import logging
import asyncio

from homeassistant.components.sensor import SensorEntity
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
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval

from . import DOMAIN  # Import DOMAIN from __init__.py
from easunpy.async_isolar import AsyncISolar

_LOGGER = logging.getLogger(__name__)

DEFAULT_SCAN_INTERVAL = 30  # Add this definition here to resolve the NameError

class DataCollector:
    """Centralized data collector for Easun Inverter."""

    def __init__(self, isolar):
        self._isolar = isolar
        self._data = {}
        self._lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._last_update_start = None
        self._last_successful_update = None
        self._update_timeout = 30
        self._sensors = []
        _LOGGER.info(f"DataCollector initialized with model: {self._isolar.model}")

    def register_sensor(self, sensor):
        """Register a sensor to be updated when data is refreshed."""
        self._sensors.append(sensor)
        _LOGGER.debug(f"Registered sensor: {sensor.name}")

    async def is_update_stuck(self) -> bool:
        """Check if the update process is stuck."""
        if self._last_update_start is None:
            return False
        
        time_since_update = (datetime.now() - self._last_update_start).total_seconds()
        return time_since_update > self._update_timeout

    async def update_data(self):
        """Fetch all data from the inverter asynchronously using bulk request."""
        if not await self._lock.acquire():
            _LOGGER.warning("Could not acquire lock for update")
            return

        try:
            # Create a task for the actual data collection
            update_task = asyncio.create_task(self._do_update())
            
            # Wait for the task with timeout
            try:
                await asyncio.wait_for(update_task, timeout=self._update_timeout)
                # Update all registered sensors after successful data fetch
                for sensor in self._sensors:
                    sensor.update_from_collector()
                _LOGGER.debug("Updated all registered sensors")
            except asyncio.TimeoutError:
                _LOGGER.error("Update timed out, cancelling task")
                update_task.cancel()
                try:
                    await update_task
                except asyncio.CancelledError:
                    _LOGGER.debug("Update task cancelled")
            
        except Exception as e:
            _LOGGER.error(f"Error during data update: {str(e)}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_consecutive_failures:
                _LOGGER.critical(f"Max consecutive failures reached ({self._max_consecutive_failures}). Stopping updates.")
                # Could add logic to notify or stop
        finally:
            self._lock.release()

    async def _do_update(self):
        """Perform the actual data update."""
        battery, pv, grid, output, status = await self._isolar.get_all_data()
        self._data["battery"] = battery.__dict__ if battery else None
        self._data["pv"] = pv.__dict__ if pv else None
        self._data["grid"] = grid.__dict__ if grid else None
        self._data["output"] = output.__dict__ if output else None
        self._data["system"] = status.__dict__ if status else None
        self._last_successful_update = datetime.now()
        self._consecutive_failures = 0

    def get_data(self, section: str, key: str):
        """Get data from a specific section and key."""
        section_data = self._data.get(section)
        if section_data:
            return section_data.get(key)
        return None

class EasunSensor(SensorEntity):
    """Representation of an Easun Inverter sensor."""

    def __init__(self, data_collector: DataCollector, unique_id: str, name: str, unit: str | None, section: str, key: str, converter=None):
        """Initialize the sensor."""
        self._collector = data_collector
        self._unique_id = unique_id
        self._name = name
        self._unit = unit
        self._section = section
        self._key = key
        self._converter = converter
        self._state = None
        self._collector.register_sensor(self)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"easun_{self._unique_id}"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self) -> str | None:
        """Return the unit of measurement."""
        return self._unit

    def update_from_collector(self):
        """Update the sensor state from the data collector."""
        value = self._collector.get_data(self._section, self._key)
        if self._converter:
            value = self._converter(value)
        self._state = value

class RegisterScanSensor(SensorEntity):
    """Sensor for register scan results."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the sensor."""
        self._attr_name = "Easun Register Scan"
        self._attr_unique_id = "easun_register_scan"
        self._state = None
        self._hass = hass

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if DOMAIN in self._hass.data and "register_scan" in self._hass.data[DOMAIN]:
            return self._hass.data[DOMAIN]["register_scan"]
        return None

    def update(self):
        """Update the sensor."""
        if DOMAIN in self._hass.data and "register_scan" in self._hass.data[DOMAIN]:
            self._state = self._hass.data[DOMAIN]["register_scan"].get("timestamp", "No scan")
        else:
            self._state = "No scan"

class DeviceScanSensor(SensorEntity):
    """Sensor for device ID scan results."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the sensor."""
        self._attr_name = "Easun Device Scan"
        self._attr_unique_id = "easun_device_scan"
        self._state = None
        self._hass = hass

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if DOMAIN in self._hass.data and "device_scan" in self._hass.data[DOMAIN]:
            return self._hass.data[DOMAIN]["device_scan"]
        return None

    def update(self):
        """Update the sensor."""
        if DOMAIN in self._hass.data and "device_scan" in self._hass.data[DOMAIN]:
            self._state = self._hass.data[DOMAIN]["device_scan"].get("timestamp", "No scan")
        else:
            self._state = "No scan"

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    """Set up the Easun Inverter sensors from a config entry."""
    entry_id = config_entry.entry_id
    inverter_ip = config_entry.data["inverter_ip"]
    local_ip = config_entry.data["local_ip"]
    model = config_entry.data["model"]
    scan_interval = config_entry.data.get("scan_interval", DEFAULT_SCAN_INTERVAL)
    
    isolar = AsyncISolar(inverter_ip, local_ip, model)
    data_collector = DataCollector(isolar)
    
    # Store the coordinator in the domain data
    hass.data[DOMAIN][entry_id] = {"coordinator": data_collector}
    
    frequency_converter = lambda value: value / 100 if value else None
    
    entities = [
        EasunSensor(data_collector, "battery_voltage", "Battery Voltage", UnitOfElectricPotential.VOLT, "battery", "voltage"),
        EasunSensor(data_collector, "battery_current", "Battery Current", UnitOfElectricCurrent.AMPERE, "battery", "current"),
        EasunSensor(data_collector, "battery_power", "Battery Power", UnitOfPower.WATT, "battery", "power"),
        EasunSensor(data_collector, "battery_soc", "Battery SoC", PERCENTAGE, "battery", "soc"),
        EasunSensor(data_collector, "battery_temperature", "Battery Temperature", UnitOfTemperature.CELSIUS, "battery", "temperature"),
        EasunSensor(data_collector, "pv_total_power", "PV Total Power", UnitOfPower.WATT, "pv", "total_power"),
        EasunSensor(data_collector, "pv_charging_power", "PV Charging Power", UnitOfPower.WATT, "pv", "charging_power"),
        EasunSensor(data_collector, "pv_charging_current", "PV Charging Current", UnitOfElectricCurrent.AMPERE, "pv", "charging_current"),
        EasunSensor(data_collector, "pv_temperature", "PV Temperature", UnitOfTemperature.CELSIUS, "pv", "temperature"),
        EasunSensor(data_collector, "pv1_voltage", "PV1 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv1_voltage"),
        EasunSensor(data_collector, "pv1_current", "PV1 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv1_current"),
        EasunSensor(data_collector, "pv1_power", "PV1 Power", UnitOfPower.WATT, "pv", "pv1_power"),
        EasunSensor(data_collector, "pv2_voltage", "PV2 Voltage", UnitOfElectricPotential.VOLT, "pv", "pv2_voltage"),
        EasunSensor(data_collector, "pv2_current", "PV2 Current", UnitOfElectricCurrent.AMPERE, "pv", "pv2_current"),
        EasunSensor(data_collector, "pv2_power", "PV2 Power", UnitOfPower.WATT, "pv", "pv2_power"),
        EasunSensor(data_collector, "pv_energy_today", "PV Generated Today", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_today"),
        EasunSensor(data_collector, "pv_energy_total", "PV Generated Total", UnitOfEnergy.KILO_WATT_HOUR, "pv", "pv_generated_total"),
        EasunSensor(data_collector, "grid_voltage", "Grid Voltage", UnitOfElectricPotential.VOLT, "grid", "voltage"),
        EasunSensor(data_collector, "grid_power", "Grid Power", UnitOfPower.WATT, "grid", "power"),
        EasunSensor(data_collector, "grid_frequency", "Grid Frequency", UnitOfFrequency.HERTZ, "grid", "frequency", frequency_converter),
        EasunSensor(data_collector, "output_voltage", "Output Voltage", UnitOfElectricPotential.VOLT, "output", "voltage"),
        EasunSensor(data_collector, "output_current", "Output Current", UnitOfElectricCurrent.AMPERE, "output", "current"),
        EasunSensor(data_collector, "output_power", "Output Power", UnitOfPower.WATT, "output", "power"),
        EasunSensor(data_collector, "output_apparent_power", "Output Apparent Power", UnitOfApparentPower.VOLT_AMPERE, "output", "apparent_power"),
        EasunSensor(data_collector, "output_load_percentage", "Output Load Percentage", PERCENTAGE, "output", "load_percentage"),
        EasunSensor(data_collector, "output_frequency", "Output Frequency", UnitOfFrequency.HERTZ, "output", "frequency", frequency_converter),
        EasunSensor(data_collector, "operating_mode", "Operating Mode", None, "system", "mode_name"),
        EasunSensor(data_collector, "inverter_time", "Inverter Time", None, "system", "inverter_time"),
        RegisterScanSensor(hass),
        DeviceScanSensor(hass),
    ]
    
    add_entities(entities, False)  # Set update_before_add to False since we're managing updates separately
    
    # Schedule periodic updates
    is_updating = False

    async def update_data_collector(now):
        """Update data collector."""
        nonlocal is_updating
        
        if is_updating:
            if await data_collector.is_update_stuck():
                _LOGGER.warning("Previous update appears to be stuck, forcing a new update")
                is_updating = False
            else:
                _LOGGER.debug("Update already in progress, skipping this cycle")
                return

        _LOGGER.debug("Starting data collector update")
        is_updating = True
        data_collector._last_update_start = datetime.now()
        
        try:
            # Use wait_for here as well for extra safety
            await asyncio.wait_for(
                data_collector.update_data(),
                timeout=data_collector._update_timeout + 5  # Add a small buffer
            )
        except asyncio.TimeoutError:
            _LOGGER.error("Update operation timed out at scheduler level")
        except Exception as e:
            _LOGGER.error(f"Error updating data collector: {str(e)}")
        finally:
            is_updating = False
            data_collector._last_update_start = None
            _LOGGER.debug("Data collector update finished")

    # Store the update function in the domain data
    hass.data[DOMAIN][config_entry.entry_id]["update_function"] = update_data_collector
    hass.data[DOMAIN][config_entry.entry_id]["scan_interval"] = scan_interval
    
    # Create the update listener and store it in the domain data
    update_listener = async_track_time_interval(
        hass, 
        update_data_collector, 
        timedelta(seconds=scan_interval)
    )
    hass.data[DOMAIN][config_entry.entry_id]["update_listener"] = update_listener
    
    # Register config entry unload function to clean up resources
    config_entry.async_on_unload(
        lambda: hass.data[DOMAIN][config_entry.entry_id]["update_listener"]()
    )
    
    _LOGGER.debug("Easun Inverter sensors added")
