"""Number entities to change numeric inverter settings."""
from __future__ import annotations

import logging
from typing import Optional

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


class _BaseNumber(NumberEntity):
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        name: str,
        system_key: str,
        min_value: float,
        max_value: float,
        step: float,
        unit: str | None,
    ):
        self._hass = hass
        self._entry_id = entry_id
        self._attr_name = name
        self._attr_unique_id = f"easun_number_{system_key}"
        self._key = system_key
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_native_value: Optional[float] = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "easun_inverter")},
            name="Easun Inverter",
            manufacturer="Easun",
        )

    @property
    def native_value(self) -> Optional[float]:
        store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        coord = store.get("coordinator")
        if not coord:
            return self._attr_native_value
        value = coord.get_data("system", self._key)
        try:
            self._attr_native_value = float(value) if value is not None else None
        except Exception:
            pass
        return self._attr_native_value

    async def _get_isolar(self):
        store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        coord = store.get("coordinator")
        if not coord:
            return None, None
        return coord._isolar, coord


# --- concrete numbers ---

class BatteryRechargeVoltage(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_battery_recharge_voltage(value)
        _LOGGER.info("Set Battery Re-Charge Voltage -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class BatteryRedischargeVoltage(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_battery_redischarge_voltage(value)
        _LOGGER.info("Set Battery Re-Discharge Voltage -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class BatteryCutoffVoltage(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_battery_cutoff_voltage(value)
        _LOGGER.info("Set Battery Cut-Off Voltage -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class BatteryBulkVoltage(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_battery_bulk_voltage(value)
        _LOGGER.info("Set Battery Bulk/CV Voltage -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class BatteryFloatVoltage(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_battery_float_voltage(value)
        _LOGGER.info("Set Battery Float Voltage -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class CVStageMaxTime(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        value = round(value / 5) * 5  # ensure multiple of 5
        ok = await isolar.set_cv_stage_max_time(int(value))
        _LOGGER.info("Set Max Charging Time at CV -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class MaxChargingCurrent(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_max_charging_current(int(value), parallel_id=0)
        _LOGGER.info("Set Max Charging Current -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class MaxUtilityChargingCurrent(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_max_utility_charging_current(int(value), parallel_id=0)
        _LOGGER.info("Set Max Utility Charging Current -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class MaxDischargeCurrent(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_max_discharge_current(int(value))
        _LOGGER.info("Set Max Discharge Current -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


# Equalization parameters

class EqTimeMinutes(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        value = round(value / 5) * 5
        ok = await isolar.equalization_set_time(int(value))
        _LOGGER.info("Equalization set time -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class EqPeriodDays(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.equalization_set_period(int(value))
        _LOGGER.info("Equalization set period -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class EqVoltage(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.equalization_set_voltage(float(value))
        _LOGGER.info("Equalization set voltage -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


class EqOvertimeMinutes(_BaseNumber):
    async def async_set_native_value(self, value: float) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        value = round(value / 5) * 5
        ok = await isolar.equalization_set_over_time(int(value))
        _LOGGER.info("Equalization set over-time -> %s", ok)
        if ok:
            await coord.update_data()
            self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    entry_id = entry.entry_id
    entities: list[NumberEntity] = [
        BatteryRechargeVoltage(hass, entry_id, "Battery Re-Charge Voltage", "battery_recharge_voltage", 22.0, 62.0, 0.1, "V"),
        BatteryRedischargeVoltage(hass, entry_id, "Battery Re-Discharge Voltage", "battery_redischarge_voltage", 0.0, 62.0, 0.1, "V"),
        BatteryCutoffVoltage(hass, entry_id, "Battery Cut-Off Voltage", "battery_undervoltage", 36.0, 48.0, 0.1, "V"),
        BatteryBulkVoltage(hass, entry_id, "Battery Bulk/CV Voltage", "battery_bulk_voltage", 24.0, 64.0, 0.1, "V"),
        BatteryFloatVoltage(hass, entry_id, "Battery Float Voltage", "battery_float_voltage", 24.0, 64.0, 0.1, "V"),
        CVStageMaxTime(hass, entry_id, "Max Charging Time at CV", "max_charging_time_cv", 0, 900, 5, "min"),
        MaxChargingCurrent(hass, entry_id, "Max Charging Current", "max_charging_current", 0, 150, 1, "A"),
        MaxUtilityChargingCurrent(hass, entry_id, "Max Utility Charging Current", "max_ac_charging_current", 0, 30, 1, "A"),
        MaxDischargeCurrent(hass, entry_id, "Max Discharging Current", "max_discharging_current", 0, 150, 1, "A"),
        # Equalization
        EqTimeMinutes(hass, entry_id, "Equalization Time", "eq_time", 5, 900, 5, "min"),
        EqPeriodDays(hass, entry_id, "Equalization Period", "eq_period", 0, 90, 1, "days"),
        EqVoltage(hass, entry_id, "Equalization Voltage", "eq_voltage", 24.0, 64.0, 0.01, "V"),
        EqOvertimeMinutes(hass, entry_id, "Equalization Over-time", "eq_overtime", 5, 900, 5, "min"),
    ]
    add_entities(entities)
    _LOGGER.debug("Number entities added")
