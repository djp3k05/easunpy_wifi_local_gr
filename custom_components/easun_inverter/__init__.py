"""
custom_components.easun_inverter
--------------------------------
Domain bootstrap + service registration for settings control.
Reading/sensor logic remains in sensor.py; this file only wires services
to the new Settings API in easunpy.async_isolar.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform

from .const import DOMAIN  # if you already have a const.py; otherwise inline "easun_inverter"
from easunpy.async_isolar import AsyncISolar

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    # Ensure data bag
    hass.data.setdefault(DOMAIN, {})
    # Create (or reuse) API used by sensors and by services.
    # We assume your config_flow stores local_ip in entry.data["local_ip"] (adapt if different).
    local_ip = entry.data.get("local_ip", "0.0.0.0")
    port = entry.data.get("port", 502)

    api = AsyncISolar(local_ip=local_ip, port=port)
    hass.data[DOMAIN][entry.entry_id] = {"api": api}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ----------------------------
    # Service registration
    # ----------------------------
    async def _svc_send_ascii(call: ServiceCall):
        cmd = call.data["command"]
        ok = await api.apply(cmd)
        _LOGGER.info("send_ascii_setting(%s) -> %s", cmd, "ACK" if ok else "NAK")

    async def _svc_set_out_src(call: ServiceCall):
        ok = await api.set_output_source_priority(call.data["mode"])
        _LOGGER.info("set_output_source_priority -> %s", ok)

    async def _svc_set_chg_src(call: ServiceCall):
        ok = await api.set_charger_source_priority(call.data["mode"])
        _LOGGER.info("set_charger_source_priority -> %s", ok)

    async def _svc_set_grid(call: ServiceCall):
        ok = await api.set_grid_working_range(call.data["mode"])
        _LOGGER.info("set_grid_working_range -> %s", ok)

    async def _svc_set_batt_type(call: ServiceCall):
        ok = await api.set_battery_type(call.data["battery_type"])
        _LOGGER.info("set_battery_type -> %s", ok)

    async def _svc_set_out_mode(call: ServiceCall):
        ok = await api.set_output_mode(call.data["mode"])
        _LOGGER.info("set_output_mode -> %s", ok)

    async def _svc_set_v(call: ServiceCall):
        ok = await api.set_rated_output_voltage(call.data["volts"])
        _LOGGER.info("set_rated_output_voltage -> %s", ok)

    async def _svc_set_f(call: ServiceCall):
        ok = await api.set_rated_output_frequency(call.data["hz"])
        _LOGGER.info("set_rated_output_frequency -> %s", ok)

    async def _svc_set_mnchg(call: ServiceCall):
        ok = await api.set_max_charging_current(call.data["amps"], call.data.get("parallel_id", 0))
        _LOGGER.info("set_max_charging_current -> %s", ok)

    async def _svc_set_muchg(call: ServiceCall):
        ok = await api.set_max_utility_charging_current(call.data["amps"], call.data.get("parallel_id", 0))
        _LOGGER.info("set_max_utility_charging_current -> %s", ok)

    async def _svc_set_pbcv(call: ServiceCall):
        ok = await api.set_battery_recharge_voltage(call.data["volts"])
        _LOGGER.info("set_battery_recharge_voltage -> %s", ok)

    async def _svc_set_pbdv(call: ServiceCall):
        ok = await api.set_battery_redischarge_voltage(call.data["volts"])
        _LOGGER.info("set_battery_redischarge_voltage -> %s", ok)

    async def _svc_set_psdv(call: ServiceCall):
        ok = await api.set_battery_cutoff_voltage(call.data["volts"])
        _LOGGER.info("set_battery_cutoff_voltage -> %s", ok)

    async def _svc_set_pcvv(call: ServiceCall):
        ok = await api.set_battery_bulk_voltage(call.data["volts"])
        _LOGGER.info("set_battery_bulk_voltage -> %s", ok)

    async def _svc_set_pbft(call: ServiceCall):
        ok = await api.set_battery_float_voltage(call.data["volts"])
        _LOGGER.info("set_battery_float_voltage -> %s", ok)

    async def _svc_set_pcvt(call: ServiceCall):
        ok = await api.set_cv_stage_max_time(call.data["minutes"])
        _LOGGER.info("set_cv_stage_max_time -> %s", ok)

    async def _svc_set_time(call: ServiceCall):
        ok = await api.set_datetime()  # optional explicit time field could be parsed
        _LOGGER.info("set_datetime(now) -> %s", ok)

    async def _svc_eq_enable(call: ServiceCall):
        ok = await api.equalization_enable(call.data["enabled"])
        _LOGGER.info("equalization_enable -> %s", ok)

    async def _svc_eq_time(call: ServiceCall):
        ok = await api.equalization_set_time(call.data["minutes"])
        _LOGGER.info("equalization_set_time -> %s", ok)

    async def _svc_eq_period(call: ServiceCall):
        ok = await api.equalization_set_period(call.data["days"])
        _LOGGER.info("equalization_set_period -> %s", ok)

    async def _svc_eq_voltage(call: ServiceCall):
        ok = await api.equalization_set_voltage(call.data["volts"])
        _LOGGER.info("equalization_set_voltage -> %s", ok)

    async def _svc_eq_overtime(call: ServiceCall):
        ok = await api.equalization_set_over_time(call.data["minutes"])
        _LOGGER.info("equalization_set_over_time -> %s", ok)

    async def _svc_eq_now(call: ServiceCall):
        ok = await api.equalization_activate_now(call.data["active"])
        _LOGGER.info("equalization_activate_now -> %s", ok)

    async def _svc_pbatcd(call: ServiceCall):
        ok = await api.battery_control(call.data["a"], call.data["b"], call.data["c"])
        _LOGGER.info("battery_control -> %s", ok)

    async def _svc_pbmaxdisc(call: ServiceCall):
        ok = await api.set_max_discharge_current(call.data["amps"])
        _LOGGER.info("set_max_discharge_current -> %s", ok)

    async def _svc_flag(call: ServiceCall):
        ok = await api.flag_set(call.data["flag"], call.data["enable"])
        _LOGGER.info("flag_set -> %s", ok)

    async def _svc_rtey(call: ServiceCall):
        ok = await api.reset_pv_load_energy()
        _LOGGER.info("reset_pv_load_energy -> %s", ok)

    async def _svc_rtdl(call: ServiceCall):
        ok = await api.erase_data_log()
        _LOGGER.info("erase_data_log -> %s", ok)

    # Register services
    hass.services.async_register(DOMAIN, "send_ascii_setting", _svc_send_ascii)
    hass.services.async_register(DOMAIN, "set_output_source_priority", _svc_set_out_src)
    hass.services.async_register(DOMAIN, "set_charger_source_priority", _svc_set_chg_src)
    hass.services.async_register(DOMAIN, "set_grid_working_range", _svc_set_grid)
    hass.services.async_register(DOMAIN, "set_battery_type", _svc_set_batt_type)
    hass.services.async_register(DOMAIN, "set_output_mode", _svc_set_out_mode)
    hass.services.async_register(DOMAIN, "set_output_rated_voltage", _svc_set_v)
    hass.services.async_register(DOMAIN, "set_output_rated_frequency", _svc_set_f)
    hass.services.async_register(DOMAIN, "set_max_charging_current", _svc_set_mnchg)
    hass.services.async_register(DOMAIN, "set_max_utility_charging_current", _svc_set_muchg)
    hass.services.async_register(DOMAIN, "set_battery_recharge_voltage", _svc_set_pbcv)
    hass.services.async_register(DOMAIN, "set_battery_redischarge_voltage", _svc_set_pbdv)
    hass.services.async_register(DOMAIN, "set_battery_cutoff_voltage", _svc_set_psdv)
    hass.services.async_register(DOMAIN, "set_battery_bulk_voltage", _svc_set_pcvv)
    hass.services.async_register(DOMAIN, "set_battery_float_voltage", _svc_set_pbft)
    hass.services.async_register(DOMAIN, "set_cv_stage_max_time", _svc_set_pcvt)
    hass.services.async_register(DOMAIN, "set_datetime", _svc_set_time)
    hass.services.async_register(DOMAIN, "equalization_enable", _svc_eq_enable)
    hass.services.async_register(DOMAIN, "equalization_set_time", _svc_eq_time)
    hass.services.async_register(DOMAIN, "equalization_set_period", _svc_eq_period)
    hass.services.async_register(DOMAIN, "equalization_set_voltage", _svc_eq_voltage)
    hass.services.async_register(DOMAIN, "equalization_set_over_time", _svc_eq_overtime)
    hass.services.async_register(DOMAIN, "equalization_activate_now", _svc_eq_now)
    hass.services.async_register(DOMAIN, "battery_control", _svc_pbatcd)
    hass.services.async_register(DOMAIN, "set_max_discharge_current", _svc_pbmaxdisc)
    hass.services.async_register(DOMAIN, "flag_set", _svc_flag)
    hass.services.async_register(DOMAIN, "reset_pv_load_energy", _svc_rtey)
    hass.services.async_register(DOMAIN, "erase_data_log", _svc_rtdl)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    api: AsyncISolar = hass.data[DOMAIN][entry.entry_id]["api"]
    await api.stop()
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
