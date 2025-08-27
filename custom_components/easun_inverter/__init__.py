# custom_components/easun_inverter/__init__.py

"""Easun / Voltronic ASCII Inverter - integration bootstrap."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.helpers.event import async_track_time_interval

# ** THE FIX IS HERE: Using the full import path **
from custom_components.easun_inverter.easunpy.async_isolar import AsyncISolar
from .sensor import DataCollector

DOMAIN = "easun_inverter"
_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]
DEFAULT_SCAN_INTERVAL = 30  # seconds

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    inverter_ip = entry.data["inverter_ip"]
    local_ip = entry.data["local_ip"]
    model = entry.data["model"]
    scan_interval = entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)

    isolar = AsyncISolar(inverter_ip, local_ip, model)
    coordinator = DataCollector(isolar)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    
    is_updating = False
    async def async_update_data(now=None):
        """Fetch data from the inverter."""
        nonlocal is_updating
        if is_updating:
            if not await coordinator.is_update_stuck():
                return
            _LOGGER.warning("Previous update stuck, forcing new cycle")
        
        is_updating = True
        try:
            await coordinator.update_data()
        finally:
            is_updating = False

    update_listener = async_track_time_interval(
        hass, async_update_data, timedelta(seconds=scan_interval)
    )
    hass.data[DOMAIN][entry.entry_id]["update_listener"] = update_listener

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _get_isolar_from_coord():
        return hass.data[DOMAIN][entry.entry_id]["coordinator"]._isolar

    async def send_ascii_setting(call: ServiceCall):
        isolar = await _get_isolar_from_coord()
        if not isolar or not hasattr(isolar, "apply_setting"):
            _LOGGER.error("Settings API not ready")
            return
        cmd = call.data["command"]
        ok = await isolar.apply_setting(cmd)
        _LOGGER.info("send_ascii_setting %s -> %s", cmd, "ACK" if ok else "NAK")
        coordinator.update_last_command_status(ok)

    hass.services.async_register(DOMAIN, "send_ascii_setting", send_ascii_setting)

    _LOGGER.debug("Entry set up for %s", DOMAIN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    if unload_ok:
        listener = hass.data[DOMAIN][entry.entry_id].get("update_listener")
        if listener:
            listener()
        
        isolar = hass.data[DOMAIN][entry.entry_id]["coordinator"]._isolar
        if isolar:
            await isolar.client.stop()
        
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok