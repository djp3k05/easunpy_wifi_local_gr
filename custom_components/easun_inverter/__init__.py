# __init__.py full code
"""The Easun ISolar Inverter integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
import logging
from easunpy.modbusclient import create_request 
from datetime import datetime
import json
import os
from aiofiles import open as async_open
from aiofiles.os import makedirs
import asyncio

_LOGGER = logging.getLogger(__name__)

DOMAIN = "easun_inverter"

# List of platforms to support. There should be a matching .py file for each,
# eg. switch.py and sensor.py
PLATFORMS: list[Platform] = [Platform.SENSOR]

# Use config_entry_only_config_schema since we only support config flow
CONFIG_SCHEMA = cv.config_entry_only_config_schema("easun_inverter")

async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version < 4:
        new_data = {**config_entry.data}
        
        # Add model with default value if it doesn't exist
        if "model" not in new_data:
            new_data["model"] = "ISOLAR_SMG_II_11K"
            
        # Update the entry with new data and version
        hass.config_entries.async_update_entry(
            config_entry,
            data=new_data,
            version=4
        )
        _LOGGER.info("Migration to version %s successful", 4)

    return True

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Easun ISolar Inverter component."""
    _LOGGER.debug("Setting up Easun ISolar Inverter component")
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Easun ISolar Inverter from a config entry."""
    if entry.version < 4:
        if not await async_migrate_entry(hass, entry):
            return False

    model = entry.data["model"]  # No default - should be required
    _LOGGER.warning(f"Setting up inverter with model: {model}, config data: {entry.data}")
    
    # Initialize domain data
    hass.data.setdefault(DOMAIN, {})
    
    async def handle_register_scan(call: ServiceCall) -> None:
        """Handle the register scan service."""
        start = call.data.get("start_register", 0)
        count = call.data.get("register_count", 5)
        
        # Get the coordinator from the entry we stored in sensor.py
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if not entry_data or "coordinator" not in entry_data:
            _LOGGER.error("No coordinator found. Is the integration set up?")
            return
        
        coordinator = entry_data["coordinator"]
        
        # Create directory if needed
        output_dir = hass.config.path("www/register_scans")
        await makedirs(output_dir, exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"{output_dir}/register_scan_{timestamp}.json"
        
        # Perform the scan
        results = []
        transaction_id = 0x0001  # Starting transaction ID
        
        for reg in range(start, start + count):
            try:
                request = create_request(transaction_id, 0x0001, 0xFF, 0x03, reg, 1)
                response = await coordinator._isolar.client.send_bulk([request])
                
                if response and response[0]:
                    decoded = decode_modbus_response(response[0], 1, "Int")
                    results.append({
                        "register": reg,
                        "value": decoded[0] if decoded else None,
                        "request": request,
                        "response": response[0]
                    })
                else:
                    results.append({
                        "register": reg,
                        "value": None,
                        "request": request,
                        "response": None
                    })
            except Exception as e:
                results.append({
                    "register": reg,
                    "value": None,
                    "error": str(e)
                })
            
            transaction_id += 1
            await asyncio.sleep(0.1)  # Small delay to avoid overwhelming the inverter
        
        # Save results to file
        scan_data = {
            "timestamp": timestamp,
            "start_register": start,
            "count": count,
            "results": results
        }
        
        async with async_open(output_file, "w") as f:
            await f.write(json.dumps(scan_data, indent=2))
        
        hass.data[DOMAIN]["register_scan"] = scan_data
        _LOGGER.info(f"Register scan complete. Results saved to {output_file}")

    async def handle_device_scan(call: ServiceCall) -> None:
        """Handle the device ID scan service."""
        start_id = call.data.get("start_id", 0)
        end_id = call.data.get("end_id", 255)
        
        # Get the coordinator
        entry_data = hass.data[DOMAIN].get(entry.entry_id)
        if not entry_data or "coordinator" not in entry_data:
            _LOGGER.error("No coordinator found. Is the integration set up?")
            return
        
        coordinator = entry_data["coordinator"]
        
        # Perform the scan
        results = []
        transaction_id = 0x0001
        
        for device_id in range(start_id, end_id + 1):
            try:
                # Create a simple read request for register 0x0000 (or any valid)
                request = create_request(transaction_id, 0x0001, device_id, 0x03, 0x0000, 1)
                
                response = await coordinator._isolar.client.send_bulk([request])
                
                result = {
                    "device_id": device_id,
                    "hex": f"0x{device_id:02x}",
                    "request": request,
                    "response": response[0] if response else None
                }
                
                if response and response[0]:
                    response_hex = response[0]
                    # Check if it's a valid Modbus response (function code match, etc.)
                    if len(response_hex) >= 18 and response_hex[12:14] == "ff" and response_hex[14:16] == "03":  # Assuming unit FF, func 03
                        result["status"] = "Valid Response"
                        result["decoded"] = decode_modbus_response(response_hex, 1, "Int")
                        _LOGGER.debug(f"Device 0x{device_id:02x} gave valid response: {response_hex}")
                    else:
                        result["status"] = "Invalid Response"
                        _LOGGER.warning(f"Device 0x{device_id:02x} gave invalid or protocol error: {response_hex}")
                else:
                    _LOGGER.debug(f"Device 0x{device_id:02x} gave no response")
                    result["status"] = "No Response"
                
                results.append(result)
                
            except Exception as e:
                _LOGGER.debug(f"Error with device ID {device_id:02x}: {e}")
                results.append({
                    "device_id": device_id,
                    "hex": f"0x{device_id:02x}",
                    "status": f"Error: {str(e)}",
                    "request": request if 'request' in locals() else None,
                    "response": None
                })
            
            await asyncio.sleep(0.1)  # Small delay between requests
        
        # Store results
        scan_data = {
            "timestamp": datetime.now().isoformat(),
            "results": results,
            "start_id": start_id,
            "end_id": end_id
        }
        hass.data[DOMAIN]["device_scan"] = scan_data
        
        # Log summary
        valid_responses = [r for r in results if r["status"] == "Valid Response"]
        _LOGGER.info(f"Device scan complete. Found {len(valid_responses)} valid responses")
        for r in valid_responses:
            _LOGGER.info(f"Device {r['hex']}: Request={r['request']}, Response={r['response']}, Decoded={r.get('decoded')}")

    # Register both services
    hass.services.async_register(DOMAIN, "register_scan", handle_register_scan)
    hass.services.async_register(DOMAIN, "device_scan", handle_device_scan)
    
    # Forward the setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Easun ISolar Inverter config entry")
    
    # Cleanup any update listeners
    if entry.entry_id in hass.data[DOMAIN]:
        if "update_listener" in hass.data[DOMAIN][entry.entry_id]:
            _LOGGER.debug("Cancelling update listener")
            hass.data[DOMAIN][entry.entry_id]["update_listener"]()
        
        # Cleanup the modbus client connection
        if "coordinator" in hass.data[DOMAIN][entry.entry_id]:
            coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
            await coordinator._isolar.client._cleanup_server()
            _LOGGER.debug("Cleaned up modbus client connection")
    
    # Unload the sensor platform
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    
    # Clean up domain data
    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        _LOGGER.debug("Removing entry data")
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok
