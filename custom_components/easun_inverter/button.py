"""Button entities for one-shot actions."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


class _BaseButton(ButtonEntity):
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry_id: str, name: str, key: str):
        self._hass = hass
        self._entry_id = entry_id
        self._attr_name = name
        self._attr_unique_id = f"easun_button_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, "easun_inverter")},
            name="Easun Inverter",
            manufacturer="Easun",
        )

    async def _get_isolar(self):
        store = self._hass.data.get(DOMAIN, {}).get(self._entry_id, {})
        coord = store.get("coordinator")
        if not coord:
            return None, None
        return coord._isolar, coord


class SyncTimeButton(_BaseButton):
    async def async_press(self) -> None:
        isolar, coord = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.set_datetime()
        _LOGGER.info("Sync inverter time -> %s", ok)
        if ok:
            await coord.update_data()


class ResetEnergyButton(_BaseButton):
    async def async_press(self) -> None:
        isolar, _ = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.reset_pv_load_energy()
        _LOGGER.info("Reset PV/Load energy -> %s", ok)


class EraseLogButton(_BaseButton):
    async def async_press(self) -> None:
        isolar, _ = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.erase_data_log()
        _LOGGER.info("Erase data log -> %s", ok)


class EqualizationNowButton(_BaseButton):
    async def async_press(self) -> None:
        isolar, _ = await self._get_isolar()
        if not isolar:
            return
        ok = await isolar.equalization_activate_now(True)
        _LOGGER.info("Equalization activate now -> %s", ok)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    entry_id = entry.entry_id
    entities = [
        SyncTimeButton(hass, entry_id, "Sync Inverter Time", "sync_time"),
        ResetEnergyButton(hass, entry_id, "Reset PV/Load Energy", "reset_energy"),
        EraseLogButton(hass, entry_id, "Erase Data Log", "erase_log"),
        EqualizationNowButton(hass, entry_id, "Equalization Now", "eq_now"),
    ]
    add_entities(entities)
    _LOGGER.debug("Button entities added")
