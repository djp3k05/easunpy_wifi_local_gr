"""Easun Inverter – setup."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    _LOGGER.debug("Setting up %s component", DOMAIN)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # Nothing heavy here; platform modules create the client/collector once
    for platform in PLATFORMS:
        hass.async_create_task(hass.config_entries.async_forward_entry_setup(entry, platform))

    _LOGGER.debug("Entry set up for %s", DOMAIN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Let the sensor platform stop the collector if it created one
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
