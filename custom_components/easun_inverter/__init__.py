"""The Easun ISolar Inverter integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
import logging
from easunpy.modbusclient import create_request, decode_modbus_response  # <-- add decode
from datetime import datetime
import json
from aiofiles import open as async_open
from aiofiles.os import makedirs
import asyncio

_LOGGER = logging.getLogger(__name__)

DOMAIN = "easun_inverter"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.BUTTON]
CONFIG_SCHEMA = cv.config_entry_only_config_schema("easun_inverter")

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    _LOGGER.debug("Migrating from version %s", config_entry.version)
    if config_entry.version < 4:
        new_data = {**config_entry.data}
        if "model" not in new_data:
            new_data["model"] = "ISOLAR_SMG_II_11K"
        hass.config_entries.async_update_entry(config_entry, data=new_data, version=4)
        _LOGGER.info("Migration to version %s successful", 4)
    return True

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    _LOGGER.debug("Setting up Easun ISolar Inverter component")
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if entry.version < 4:
        if not await async_migrate_entry(hass, entry):
            return False

    model = entry.data["model"]
    _LOGGER.warning(f"Setting up inverter with model: {model}, config data: {entry.data}")
    hass.data.setdefault(DOMAIN, {})

    async def handle_register_scan(call: ServiceCall) -> None:
        start = call.data.get("start_register", 0)
        count = call.data.get("register_count", 5)

        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if not entry_data or "coordinator" not in entry_data:
            _LOGGER.error("No coordinator found. Is the integration set up?")
            return
        coordinator = entry_data["coordinator"]

        output_dir = hass.config.path("www/register_scans")
        await makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"{output_dir}/register_scan_{timestamp}.json"

        results = []
        transaction_id = 0x0001
        for reg in range(start, start + count):
            try:
                request = create_request(transaction_id, 0x0001, 0xFF, 0x03, reg, 1)
                response = await coordinator._isolar.client.send_bulk([request])
                if response and response[0]:
                    decoded = decode_modbus_response(response[0], 1, "Int")
                    results.append({"register": reg, "value": decoded[0] if decoded else None, "request": request, "response": response[0]})
                else:
                    results.append({"register": reg, "value": None, "request": request, "response": None})
            except Exception as e:
                results.append({"register": reg, "value": None, "error": str(e)})
            transaction_id += 1
            await asyncio.sleep(0.1)

        scan_data = {"timestamp": timestamp, "start_register": start, "count": count, "results": results}
        async with async_open(output_file, "w") as f:
            await f.write(json.dumps(scan_data, indent=2))
        hass.data[DOMAIN]["register_scan"] = scan_data
        _LOGGER.info(f"Register scan complete. Results saved to {output_file}")

    async def handle_device_scan(call: ServiceCall) -> None:
        start_id = call.data.get("start_id", 0)
        end_id = call.data.get("end_id", 255)

        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if not entry_data or "coordinator" not in entry_data:
            _LOGGER.error("No coordinator found. Is the integration set up?")
            return
        coordinator = entry_data["coordinator"]

        results = []
        transaction_id = 0x0001
        for device_id in range(start_id, end_id + 1):
            try:
                request = create_request(transaction_id, 0x0001, device_id, 0x03, 0x0000, 1)
                response = await coordinator._isolar.client.send_bulk([request])
                result = {"device_id": device_id, "hex": f"0x{device_id:02x}", "request": request, "response": response[0] if response else None}
                results.append(result)
            except Exception as e:
                results.append({"device_id": device_id, "hex": f"0x{device_id:02x}", "status": f"Error: {str(e)}", "request": request if 'request' in locals() else None, "response": None})
            await asyncio.sleep(0.1)

        scan_data = {"timestamp": datetime.now().isoformat(), "results": results, "start_id": start_id, "end_id": end_id}
        hass.data[DOMAIN]["device_scan"] = scan_data
        _LOGGER.info("Device scan complete")
    # Register services and forward setup
    hass.services.async_register(DOMAIN, "register_scan", handle_register_scan)
    hass.services.async_register(DOMAIN, "device_scan", handle_device_scan)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.debug("Unloading Easun ISolar Inverter config entry")
    if entry.entry_id in hass.data[DOMAIN]:
        if "update_listener" in hass.data[DOMAIN][entry.entry_id]:
            _LOGGER.debug("Cancelling update listener")
            hass.data[DOMAIN][entry.entry_id]["update_listener"]()
        if "coordinator" in hass.data[DOMAIN][entry.entry_id]:
            coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
            await coordinator._isolar.client._cleanup_server()
            _LOGGER.debug("Cleaned up modbus client connection")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.debug("Removing entry data")
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
