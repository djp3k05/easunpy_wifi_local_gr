"""Easun / Voltronic ASCII Inverter - integration bootstrap."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from .const import DOMAIN

DOMAIN = "easun_inverter"
_LOGGER = logging.getLogger(__name__)

# We now expose control entities:
PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    _LOGGER.debug("Setting up %s component", DOMAIN)
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})  # sensor.py will populate coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Optional: keep a couple of generic services for power users
    # They will locate the coordinator/isolar created in sensor.py at call-time.
    async def _get_isolar():
        store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        coord = store.get("coordinator")
        if not coord:
            return None
        return getattr(coord, "_isolar", None)

    async def send_ascii_setting(call: ServiceCall):
        isolar = await _get_isolar()
        if not isolar or not hasattr(isolar, "apply_setting"):
            _LOGGER.error("Settings API not ready")
            return
        cmd = call.data["command"]
        ok = await isolar.apply_setting(cmd)
        _LOGGER.info("send_ascii_setting %s -> %s", cmd, "ACK" if ok else "NAK")

    hass.services.async_register(DOMAIN, "send_ascii_setting", send_ascii_setting)

    _LOGGER.debug("Entry set up for %s", DOMAIN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
