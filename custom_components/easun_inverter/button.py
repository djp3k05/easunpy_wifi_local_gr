from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    entry_id = entry.entry_id
    async_add_entities(
        [
            SyncTimeButton(hass, entry_id),
            ResetPVLoadEnergyButton(hass, entry_id),
            EraseDataLogButton(hass, entry_id),
            EqualizationNowButton(hass, entry_id),
        ]
    )
    _LOGGER.debug("Button entities added")


class _BaseBtn(ButtonEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry_id: str):
        self._hass = hass
        self._entry_id = entry_id

    @property
    def _collector(self):
        return self._hass.data.get(DOMAIN, {}).get(self._entry_id)


class SyncTimeButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Sync Inverter Time"

    async def async_press(self) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot sync time")
            return
        ok = await c.isolar.set_datetime()
        _LOGGER.info("Sync inverter time -> %s", ok)


class ResetPVLoadEnergyButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Reset PV/Load Energy"

    async def async_press(self) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot reset energy")
            return
        ok = await c.isolar.reset_pv_load_energy()
        _LOGGER.info("Reset PV/Load Energy -> %s", ok)


class EraseDataLogButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Erase Data Log"

    async def async_press(self) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot erase data log")
            return
        ok = await c.isolar.erase_data_log()
        _LOGGER.info("Erase Data Log -> %s", ok)


class EqualizationNowButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Equalization Now"

    async def async_press(self) -> None:
        c = self._collector
        if not c:
            _LOGGER.warning("Collector not ready yet; cannot trigger equalization")
            return
        ok = await c.isolar.equalization_activate_now(True)
        _LOGGER.info("Equalization Now -> %s", ok)
