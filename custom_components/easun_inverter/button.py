from __future__ import annotations

import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    group = hass.data[DOMAIN][entry.entry_id]
    collector = group

    async_add_entities(
        [
            SyncTimeButton(collector),
            ResetPVLoadEnergyButton(collector),
            EraseDataLogButton(collector),
            EqualizationNowButton(collector),
        ]
    )
    _LOGGER.debug("Button entities added")


class _BaseBtn(ButtonEntity):
    _attr_should_poll = False

    def __init__(self, collector):
        self.collector = collector


class SyncTimeButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Sync Inverter Time"

    async def async_press(self) -> None:
        ok = await self.collector.isolar.set_datetime()
        _LOGGER.info("Sync inverter time -> %s", ok)


class ResetPVLoadEnergyButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Reset PV/Load Energy"

    async def async_press(self) -> None:
        ok = await self.collector.isolar.reset_pv_load_energy()
        _LOGGER.info("Reset PV/Load Energy -> %s", ok)


class EraseDataLogButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Erase Data Log"

    async def async_press(self) -> None:
        ok = await self.collector.isolar.erase_data_log()
        _LOGGER.info("Erase Data Log -> %s", ok)


class EqualizationNowButton(_BaseBtn):
    @property
    def name(self) -> str:
        return "Equalization Now"

    async def async_press(self) -> None:
        ok = await self.collector.isolar.equalization_activate_now(True)
        _LOGGER.info("Equalization Now -> %s", ok)
