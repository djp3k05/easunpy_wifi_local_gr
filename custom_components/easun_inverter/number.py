from __future__ import annotations

import logging
from typing import Optional, Callable

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SIGNAL_COLLECTOR_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    entry_id = entry.entry_id

    def N(name: str, unit: str, native_step: float, getter: Callable, setter: Callable, min_v: float, max_v: float):
        return SettingNumber(hass, entry_id, name, unit, native_step, getter, setter, min_v, max_v)

    entities = [
        N("Battery Re-Charge Voltage", "V", 0.1,
          lambda c: getattr(c.last_status, "battery_recharge_voltage", None),
          lambda iso, v: iso.set_battery_recharge_voltage(v),
          22.0, 62.0),

        N("Battery Re-Discharge Voltage", "V", 0.1,
          lambda c: getattr(c.last_status, "battery_redischarge_voltage", None),
          lambda iso, v: iso.set_battery_redischarge_voltage(v),
          0.0, 62.0),

        N("Battery Cut-Off Voltage", "V", 0.1,
          lambda c: None,  # not readable via QPIRI on many firmwares
          lambda iso, v: iso.set_battery_cutoff_voltage(v),
          36.0, 48.0),

        N("Battery Bulk/CV Voltage", "V", 0.1,
          lambda c: getattr(c.last_status, "battery_bulk_voltage", None),
          lambda iso, v: iso.set_battery_bulk_voltage(v),
          24.0, 64.0),

        N("Battery Float Voltage", "V", 0.1,
          lambda c: getattr(c.last_status, "battery_float_voltage", None),
          lambda iso, v: iso.set_battery_float_voltage(v),
          24.0, 64.0),

        N("Max Charging Time at CV", "min", 5,
          lambda c: getattr(c.last_status, "max_charging_time_cv", None),
          lambda iso, v: iso.set_cv_stage_max_time(int(v)),
          0, 900),

        N("Max Charging Current", "A", 1,
          lambda c: getattr(c.last_status, "max_charging_current", None),
          lambda iso, v: iso.set_max_charging_current(int(v)),
          0, 150),

        N("Max Utility Charging Current", "A", 1,
          lambda c: getattr(c.last_status, "max_ac_charging_current", None),
          lambda iso, v: iso.set_max_utility_charging_current(int(v)),
          0, 30),

        N("Max Discharging Current", "A", 1,
          lambda c: getattr(c.last_status, "max_discharging_current", None),
          lambda iso, v: iso.set_max_discharge_current(int(v)),
          0, 150),

        # Equalization values (usually not readable)
        N("Equalization Time", "min", 5,
          lambda c: None, lambda iso, v: iso.equalization_set_time(int(v)), 5, 900),
        N("Equalization Period", "days", 1,
          lambda c: None, lambda iso, v: iso.equalization_set_period(int(v)), 0, 90),
        N("Equalization Voltage", "V", 0.01,
          lambda c: None, lambda iso, v: iso.equalization_set_voltage(float(v)), 24.0, 64.0),
        N("Equalization Over Time", "min", 5,
          lambda c: None, lambda iso, v: iso.equalization_set_over_time(int(v)), 5, 900),
    ]

    async_add_entities(entities)
    _LOGGER.debug("Number entities added")


class SettingNumber(NumberEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry_id: str, name: str, unit: str, step: float, getter, setter, min_v, max_v):
        self._hass = hass
        self._entry_id = entry_id
        self._name = name
        self._unit = unit
        self._getter = getter
        self._setter = setter
        self._attr_native_step = step
        self._attr_native_min_value = min_v
        self._attr_native_max_value = max_v
        self._last_set: Optional[float] = None
        self._unsub = None

    @property
    def _collector(self):
        return self._hass.data.get(DOMAIN, {}).get(self._entry_id)

    @property
    def name(self) -> str:
        return self._name

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._unit

    @property
    def native_value(self) -> Optional[float]:
        c = self._collector
        val = None
        try:
            if c:
                val = self._getter(c)
        except Exception:  # noqa: BLE001
            val = None
        return val if val is not None else self._last_set

    async def async_set_native_value(self, value: float) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot set %s", self._name)
            return
        ok = await self._setter(c.isolar, value)
        if ok:
            self._last_set = float(value)
            self.async_write_ha_state()
            _LOGGER.info("Set %s -> %s", self._name, ok)
        else:
            _LOGGER.warning("Failed to set %s to %s", self._name, value)

    async def async_added_to_hass(self) -> None:
        @callback
        def _updated():
            self.async_write_ha_state()
        self._unsub = async_dispatcher_connect(self.hass, SIGNAL_COLLECTOR_UPDATED, _updated)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
