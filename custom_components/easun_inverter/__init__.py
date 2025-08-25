"""Easun Inverter – setup."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    _LOGGER.debug("Setting up %s component", DOMAIN)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Create domain bucket and a placeholder for this entry's shared objects.
    hass.data.setdefault(DOMAIN, {})
    # Placeholder avoids KeyError/races in platform setup; sensor will replace it with the collector.
    hass.data[DOMAIN].setdefault(entry.entry_id, None)

    # Forward setup to all platforms declared in manifest
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.debug("Entry set up for %s", DOMAIN)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Let sensor/platforms clean up; then drop our placeholder
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
