from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Iterable, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send, async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from easunpy.async_isolar import AsyncISolar

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


# ---- helpers: read attr OR dict key; plus describe for debug ----
def _get(obj: Any, keys: Iterable[str]) -> Any:
    """Return first non-None value among attribute-or-key candidates."""
    if obj is None:
        return None
    for k in keys:
        if hasattr(obj, k):
            v = getattr(obj, k)
            if v is not None:
                return v
        if isinstance(obj, dict) and k in obj and obj[k] is not None:
            return obj[k]
    return None


def _describe_obj(obj: Any) -> str:
    if obj is None:
        return "None"
    try:
        if isinstance(obj, dict):
            ks = list(obj.keys())
            return f"dict keys={ks[:25]}{'…' if len(ks) > 25 else ''}"
        # dataclass or other object: list public attrs
        attrs = [a for a in dir(obj) if not a.startswith("_") and not callable(getattr(obj, a, None))]
        return f"{type(obj).__name__} attrs={attrs[:25]}{'…' if len(attrs) > 25 else ''}"
    except Exception as e:  # noqa: BLE001
        return f"{type(obj).__name__} (describe error: {e})"


class FG:
    """Field getter callable used by sensors; also carries meta for logging & availability."""
    __slots__ = ("src_attr", "keys", "name")

    def __init__(self, src_attr: str, *keys: str, name: str = ""):
        self.src_attr = src_attr
        self.keys = keys
        self.name = name  # optional human name for debug

    def __call__(self, collector: "DataCollector"):
        obj = getattr(collector, self.src_attr, None)
        return _get(obj, self.keys)


class DataCollector:
    """Owns the AsyncISolar client and caches the last reading for other platforms."""

    def __init__(self, hass: HomeAssistant, inverter_ip: str, local_ip: str, model: str, scan_interval: int) -> None:
        self.hass = hass
        self.isolar = AsyncISolar(inverter_ip, local_ip, model=model)
        self.scan_interval = max(2, int(scan_interval or DEFAULT_SCAN_INTERVAL))

        # These can be dataclasses OR dicts depending on easunpy version
        self.last_battery: Optional[Any] = None
        self.last_pv: Optional[Any] = None
        self.last_grid: Optional[Any] = None
        self.last_output: Optional[Any] = None
        self.last_status: Optional[Any] = None

        self._task_unsub = None
        self._updating = False
        self._dumped_once = False  # only log one deep snapshot

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
            got_any = any(x is not None for x in (batt, pv, grid, out, status))
            if got_any:
                if batt is not None:
                    self.last_battery = batt
                if pv is not None:
                    self.last_pv = pv
                if grid is not None:
                    self.last_grid = grid
                if out is not None:
                    self.last_output = out
                if status is not None:
                    self.last_status = status

                # One-time deep snapshot so we know exactly what's inside
                if not self._dumped_once:
                    _LOGGER.debug(
                        "Snapshot shapes: battery=%s | pv=%s | grid=%s | output=%s | status=%s",
                        _describe_obj(self.last_battery),
                        _describe_obj(self.last_pv),
                        _describe_obj(self.last_grid),
                        _describe_obj(self.last_output),
                        _describe_obj(self.last_status),
                    )
                    # Sample a few values to confirm mapping
                    sample_batt_v = _get(self.last_battery, ("voltage", "battery_voltage"))
                    sample_out_w = _get(self.last_output, ("power", "ac_output_active_power", "output_active_power"))
                    sample_mode = _get(self.last_status, ("mode_name", "operating_mode"))
                    _LOGGER.debug(
                        "Sample values: BatteryVoltage=%s | OutputPower=%s | OperatingMode=%s",
                        sample_batt_v, sample_out_w, sample_mode,
                    )
                    self._dumped_once = True

                async_dispatcher_send(self.hass, SIGNAL_COLLECTOR_UPDATED)
                _LOGGER.debug("Updated all registered sensors")
            else:
                _LOGGER.debug("Parsed response returned no data objects this cycle")
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

    group = hass.data.setdefault(DOMAIN, {})
    collector: DataCollector = group.get(entry.entry_id)
    if collector is None:
        collector = DataCollector(hass, inverter_ip, local_ip, model, scan_interval)
        group[entry.entry_id] = collector
        await collector.start()
    _LOGGER.debug("Setting up entry with scan_interval: %s", scan_interval)

    entities: list[SensorEntity] = []

    def add(name: str, icon: str, unit: Optional[str], getter: FG):
        getter.name = name
        entities.append(GenericSensor(entry.entry_id, collector, name, icon, unit, getter))

    # Battery
    add("Battery Voltage", "mdi:car-battery", "V", FG("last_battery", "voltage", "battery_voltage"))
    add("Battery Current", "mdi:current-dc", "A", FG("last_battery", "current", "battery_current"))
    add("Battery Power", "mdi:flash", "W", FG("last_battery", "power", "battery_power"))
    add("Battery SoC", "mdi:battery-high", "%", FG("last_battery", "soc", "battery_capacity", "battery_soc"))
    add("Inverter Temperature", "mdi:thermometer", "°C", FG("last_battery", "temperature", "inverter_temperature"))
    add("Battery Charge Current", "mdi:current-dc", "A", FG("last_battery", "charge_current", "battery_charging_current"))
    add("Battery Discharge Current", "mdi:current-dc", "A", FG("last_battery", "discharge_current", "battery_discharge_current"))
    add("Battery Voltage (SCC)", "mdi:car-battery", "V", FG("last_battery", "voltage_scc", "battery_voltage_from_scc"))
    add("Battery Voltage Offset for Fans", "mdi:fan", None, FG("last_battery", "voltage_offset_for_fans"))
    add("EEPROM Version", "mdi:chip", None, FG("last_battery", "eeprom_version"))

    # PV
    add("PV Total Power", "mdi:solar-power", "W", FG("last_pv", "total_power", "pv_total_power"))
    add("PV Charging Power", "mdi:solar-power", "W", FG("last_pv", "charging_power", "pv_charging_power"))
    add("PV Charging Current", "mdi:current-dc", "A", FG("last_pv", "charging_current", "pv_charging_current"))
    add("PV1 Voltage", "mdi:solar-panel", "V", FG("last_pv", "pv1_voltage"))
    add("PV1 Current", "mdi:current-dc", "A", FG("last_pv", "pv1_current"))
    add("PV1 Power", "mdi:solar-power", "W", FG("last_pv", "pv1_power"))
    add("PV2 Voltage", "mdi:solar-panel", "V", FG("last_pv", "pv2_voltage"))
    add("PV2 Current", "mdi:current-dc", "A", FG("last_pv", "pv2_current"))
    add("PV2 Power", "mdi:solar-power", "W", FG("last_pv", "pv2_power"))
    add("PV Generated Today", "mdi:counter", None, FG("last_pv", "pv_generated_today", "pv_today"))
    add("PV Generated Total", "mdi:counter", None, FG("last_pv", "pv_generated_total", "pv_total"))

    # Grid
    add("Grid Voltage", "mdi:transmission-tower", "V", FG("last_grid", "voltage", "grid_voltage"))
    add("Grid Power", "mdi:flash", "W", FG("last_grid", "power", "grid_power"))
    add("Grid Frequency", "mdi:sine-wave", "Hz", FG("last_grid", "frequency", "grid_frequency"))

    # Output
    add("Output Voltage", "mdi:power-socket-eu", "V", FG("last_output", "voltage", "ac_output_voltage", "output_voltage"))
    add("Output Current", "mdi:current-ac", "A", FG("last_output", "current", "output_current"))
    add("Output Power", "mdi:flash", "W", FG("last_output", "power", "ac_output_active_power", "output_active_power"))
    add("Output Apparent Power", "mdi:flash", "VA", FG("last_output", "apparent_power", "ac_output_apparent_power", "output_apparent_power"))
    add("Output Load Percentage", "mdi:percent", "%", FG("last_output", "load_percentage", "output_load_percent"))
    add("Output Frequency", "mdi:sine-wave", "Hz", FG("last_output", "frequency", "ac_output_frequency", "output_frequency"))

    # System status + QPIRI (settings snapshot)
    add("Operating Mode", "mdi:power-settings", None, FG("last_status", "mode_name", "operating_mode"))
    add("Inverter Time", "mdi:clock-outline", None, FG("last_status", "inverter_time"))
    add("Bus Voltage", "mdi:resistor", "V", FG("last_status", "bus_voltage"))
    add("Device Status Flags", "mdi:flag-outline", None, FG("last_status", "device_status_flags"))
    add("Device Status 2 Flags", "mdi:flag-outline", None, FG("last_status", "device_status_flags2"))
    add("Warnings", "mdi:alert-outline", None, FG("last_status", "warnings"))

    add("Grid Rating Voltage", "mdi:alpha-g-circle", "V", FG("last_status", "grid_rating_voltage"))
    add("Grid Rating Current", "mdi:alpha-g-circle", "A", FG("last_status", "grid_rating_current"))
    add("AC Output Rating Voltage", "mdi:alpha-a-circle", "V", FG("last_status", "ac_output_rating_voltage"))
    add("AC Output Rating Frequency", "mdi:alpha-a-circle", "Hz", FG("last_status", "ac_output_rating_frequency"))
    add("AC Output Rating Current", "mdi:alpha-a-circle", "A", FG("last_status", "ac_output_rating_current"))
    add("AC Output Rating Apparent Power", "mdi:alpha-a-circle", "VA", FG("last_status", "ac_output_rating_apparent_power"))
    add("AC Output Rating Active Power", "mdi:alpha-a-circle", "W", FG("last_status", "ac_output_rating_active_power"))
    add("Battery Rating Voltage", "mdi:alpha-b-circle", "V", FG("last_status", "battery_rating_voltage"))
    add("Battery Re-Charge Voltage", "mdi:alpha-b-circle", "V", FG("last_status", "battery_recharge_voltage"))
    add("Battery Under Voltage", "mdi:alpha-b-circle", "V", FG("last_status", "battery_undervoltage"))
    add("Battery Bulk Voltage", "mdi:alpha-b-circle", "V", FG("last_status", "battery_bulk_voltage"))
    add("Battery Float Voltage", "mdi:alpha-b-circle", "V", FG("last_status", "battery_float_voltage"))
    add("Battery Type", "mdi:car-battery", None, FG("last_status", "battery_type"))
    add("Max AC Charging Current", "mdi:alpha-m-circle", "A", FG("last_status", "max_ac_charging_current"))
    add("Max Charging Current", "mdi:alpha-m-circle", "A", FG("last_status", "max_charging_current"))
    add("Input Voltage Range", "mdi:alpha-i-circle", None, FG("last_status", "input_voltage_range"))
    add("Output Source Priority", "mdi:alpha-o-circle", None, FG("last_status", "output_source_priority"))
    add("Charger Source Priority", "mdi:alpha-c-circle", None, FG("last_status", "charger_source_priority"))
    add("Parallel Max Num", "mdi:alpha-p-circle", None, FG("last_status", "parallel_max_num"))
    add("Machine Type", "mdi:cog", None, FG("last_status", "machine_type"))
    add("Topology", "mdi:vector-triangle", None, FG("last_status", "topology"))
    add("Output Mode (QPIRI)", "mdi:alpha-o-circle", None, FG("last_status", "output_mode_qpiri"))
    add("Battery Re-Discharge Voltage", "mdi:alpha-b-circle", "V", FG("last_status", "battery_redischarge_voltage"))
    add("PV OK Condition", "mdi:solar-power", None, FG("last_status", "pv_ok_condition"))
    add("PV Power Balance", "mdi:solar-power", None, FG("last_status", "pv_power_balance"))
    add("Max Charging Time at CV", "mdi:timer", "min", FG("last_status", "max_charging_time_cv"))
    add("Max Discharging Current", "mdi:current-dc", "A", FG("last_status", "max_discharging_current"))

    async_add_entities(entities)
    _LOGGER.debug("Easun Inverter sensors added (%d entities)", len(entities))

    # Force one immediate refresh AFTER entities are added so they get their first state
    await collector.async_poll_once()


class GenericSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, entry_id: str, collector: DataCollector, name: str, icon: str, unit: str | None, getter: FG):
        self._entry_id = entry_id
        self._collector = collector
        self._name = name
        self._icon = icon
        self._unit = unit
        self._getter = getter
        self._unsub = None
        self._debug_once_logged = False

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
    def available(self) -> bool:
        """Mark available when the collector has the relevant last_* object set."""
        src = getattr(self._collector, self._getter.src_attr, None)
        return src is not None

    @property
    def native_value(self):
        try:
            val = self._getter(self._collector)
            if val is None and not self._debug_once_logged:
                src_obj = getattr(self._collector, self._getter.src_attr, None)
                _LOGGER.debug(
                    "Sensor '%s' has no value yet. Source=%s, tried keys=%s",
                    self._name, _describe_obj(src_obj), list(self._getter.keys),
                )
                self._debug_once_logged = True
            return val
        except Exception as e:  # noqa: BLE001
            _LOGGER.debug("Sensor '%s' getter raised: %s", self._name, e)
            return None

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            # helpful to see that dispatcher fired and we pushed a state
            try:
                val = self.native_value
            except Exception:  # noqa: BLE001
                val = None
            _LOGGER.debug("Sensor write: %s = %s", self._name, val)
            self.async_write_ha_state()

        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_COLLECTOR_UPDATED, _updated)
        _LOGGER.debug("Sensor subscribed: %s (src=%s, keys=%s)", self._name, self._getter.src_attr, list(self._getter.keys))

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
