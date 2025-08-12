# async_isolar.py full code
import logging
from typing import List, Optional, Dict, Tuple, Any
from .async_modbusclient import AsyncModbusClient
from .modbusclient import create_request, decode_modbus_response, create_ascii_request, decode_ascii_response
from .isolar import BatteryData, PVData, GridData, OutputData, SystemStatus, OperatingMode
import datetime
from .models import MODEL_CONFIGS, ModelConfig

# Set up logging
logger = logging.getLogger(__name__)

class AsyncISolar:
    def __init__(self, inverter_ip: str, local_ip: str, model: str = "ISOLAR_SMG_II_11K", port: int = 8899):
        self.client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip, port=port)
        self._transaction_id = 0x0772
        
        if model not in MODEL_CONFIGS:
            raise ValueError(f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}")
        
        self.model = model
        self.model_config = MODEL_CONFIGS[model]
        self.port = port  # Store port for reference
        logger.warning(f"AsyncISolar initialized with model: {model} on port {port}")

    def update_model(self, model: str):
        """Update the model configuration."""
        if model not in MODEL_CONFIGS:
            raise ValueError(f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}")
        
        logger.warning(f"Updating AsyncISolar to model: {model}")
        self.model = model
        self.model_config = MODEL_CONFIGS[model]

    def _get_next_transaction_id(self) -> int:
        """Get next transaction ID and increment counter."""
        current_id = self._transaction_id
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF  # Wrap around at 0xFFFF
        return current_id

    async def _read_registers_bulk(self, register_groups: list[tuple[int, int]], data_format: str = "Int") -> list[Optional[list[int]]]:
        """Read multiple groups of registers in a single connection."""
        try:
            # Create requests for each register group
            requests = [
                create_request(self._get_next_transaction_id(), 0x0001, 0x00, 0x03, start, count)
                for start, count in register_groups
            ]
            
            logger.debug(f"Sending bulk request for register groups: {register_groups}")
            responses = await self.client.send_bulk(requests)
             
            # Initialize results array with None values
            decoded_groups = [None] * len(register_groups)
            
            # Process each response individually
            for i, (response, (_, count)) in enumerate(zip(responses, register_groups)):
                try:
                    if response:  # Only decode if we got a response
                        decoded = decode_modbus_response(response, count, data_format)
                        logger.debug(f"Decoded values for group {i}: {decoded}")
                        decoded_groups[i] = decoded
                    else:
                        logger.warning(f"No response for register group {register_groups[i]}")
                except Exception as e:
                    logger.warning(f"Failed to decode register group {register_groups[i]}: {e}")
                    # Keep None for this group
                
            return decoded_groups
            
        except Exception as e:
            logger.error(f"Error reading registers bulk: {e}")
            return [None] * len(register_groups)

    async def _get_all_data_ascii(self) -> Tuple[Optional[BatteryData], Optional[PVData], Optional[GridData], Optional[OutputData], Optional[SystemStatus]]:
        commands = ["QPIGS", "QPIGS2", "QMOD"]
        requests = [create_ascii_request(self._get_next_transaction_id(), 0x0001, cmd) for cmd in commands]
        responses = await self.client.send_bulk(requests)
        if not responses or len(responses) < 3:
            raise ValueError("Failed to get responses from inverter")
        qpigs_raw = decode_ascii_response(responses[0])
        qpigs2_raw = decode_ascii_response(responses[1])
        qmod_raw = decode_ascii_response(responses[2])
        if not qpigs_raw or not qmod_raw:
            raise ValueError("Invalid responses from inverter")
        # Remove leading '(' if present
        qpigs_raw = qpigs_raw.lstrip('(')
        qpigs2_raw = qpigs2_raw.lstrip('(') if qpigs2_raw else ""
        qmod = qmod_raw.lstrip('(')
        # Split
        qpigs_parts = qpigs_raw.split(' ')
        qpigs2_parts = qpigs2_raw.split(' ') if qpigs2_raw else []
        values = {}
        # From QPIGS
        if len(qpigs_parts) >= 21:
            values["grid_voltage"] = float(qpigs_parts[0])
            values["grid_frequency"] = float(qpigs_parts[1]) * 100  # Scale to match Modbus raw (e.g., 50.0 -> 5000)
            values["output_voltage"] = float(qpigs_parts[2])
            values["output_frequency"] = float(qpigs_parts[3]) * 100  # Scale to match Modbus raw
            values["output_apparent_power"] = int(qpigs_parts[4])
            values["output_power"] = int(qpigs_parts[5])
            values["output_load_percentage"] = int(qpigs_parts[6])
            values["battery_voltage"] = float(qpigs_parts[8])
            battery_charging_current = int(qpigs_parts[9])
            values["battery_soc"] = int(qpigs_parts[10])
            inverter_heat_sink_temp = int(qpigs_parts[11])
            pv_charging_current = float(qpigs_parts[12])
            pv1_voltage = float(qpigs_parts[13])
            battery_discharge_current = int(qpigs_parts[15])
            pv_charging_power = int(qpigs_parts[19])
            # Set temperatures (use inverter heat sink for both, as no separate PV temp in QPIGS)
            values["battery_temperature"] = inverter_heat_sink_temp
            values["pv_temperature"] = inverter_heat_sink_temp
            # PV
            values["pv_charging_current"] = pv_charging_current
            values["pv1_voltage"] = pv1_voltage
            values["pv1_power"] = pv_charging_power
            values["pv1_current"] = pv_charging_power / pv1_voltage if pv1_voltage > 0 else 0
            values["pv_total_power"] = pv_charging_power
            values["pv_charging_power"] = pv_charging_power
            # Battery current and power
            values["battery_current"] = battery_charging_current - battery_discharge_current
            values["battery_power"] = int(values["battery_voltage"] * values["battery_current"])
            # Output current approx
            values["output_current"] = values["output_power"] / values["output_voltage"] if values["output_voltage"] > 0 else 0
            # Grid power calculation
            values["grid_power"] = values["output_power"] + values["battery_power"] - values["pv_charging_power"]
            # For PV2 if available
            pv2_power = 0
            if len(qpigs2_parts) >= 3:
                pv2_current = float(qpigs2_parts[0])
                pv2_voltage = float(qpigs2_parts[1])
                pv2_power = int(qpigs2_parts[2])
                values["pv2_voltage"] = pv2_voltage
                values["pv2_current"] = pv2_current
                values["pv2_power"] = pv2_power
                values["pv_total_power"] += pv2_power
                values["pv_charging_power"] += pv2_power
                values["pv_charging_current"] += pv2_current  # sum?
            else:
                values["pv2_voltage"] = 0.0
                values["pv2_current"] = 0.0
                values["pv2_power"] = 0
            # No energy stats available from QPIGS/QPIGS2
            values["pv_energy_today"] = None
            values["pv_energy_total"] = None
        else:
            raise ValueError("Invalid QPIGS response")
        # System status
        if qmod in ['L', 'C']:
            values["operation_mode"] = 2  # SUB, utility related
            values["mode_name"] = "Line Mode" if qmod == 'L' else "Charging Mode"
        elif qmod == 'B':
            values["operation_mode"] = 3  # SBU
            values["mode_name"] = "Battery Mode"
        else:
            values["operation_mode"] = 0
            values["mode_name"] = f"UNKNOWN ({qmod})"
        values["inverter_time"] = None
        battery = self._create_battery_data(values)
        pv = self._create_pv_data(values)
        grid = self._create_grid_data(values)
        output = self._create_output_data(values)
        system = self._create_system_status(values)
        return battery, pv, grid, output, system

    async def get_all_data(self) -> Tuple[Optional[BatteryData], Optional[PVData], Optional[GridData], Optional[OutputData], Optional[SystemStatus]]:
        """Get all inverter data in a single bulk request."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()
        # Group consecutive registers to minimize requests
        register_groups = []
        current_group = None
        
        # Get all unique registers from the config, sorted
        registers = sorted(set(
            self.model_config.get_address(name) 
            for name in self.model_config.register_map.keys()
            if self.model_config.get_address(name) is not None and self.model_config.get_address(name) > 0
        ))
        
        for reg in registers:
            if current_group is None:
                current_group = (reg, 1)
            elif reg == current_group[0] + current_group[1]:
                current_group = (current_group[0], current_group[1] + 1)
            else:
                register_groups.append(current_group)
                current_group = (reg, 1)
        
        if current_group:
            register_groups.append(current_group)
        
        if not register_groups:
            logger.warning("No registers defined for model")
            return None, None, None, None, None
        
        logger.debug(f"Optimized register groups: {register_groups}")
        
        # Read all groups
        decoded_groups = await self._read_registers_bulk(register_groups)
        
        # Flatten all decoded values into a single list
        all_values = []
        for group in decoded_groups:
            if group is not None:
                all_values.extend(group)
        
        # Create a mapping of register address to value
        reg_to_value = {}
        current_reg = register_groups[0][0] if register_groups else 0
        for value in all_values:
            reg_to_value[current_reg] = value
            current_reg += 1
        
        # Process values according to config
        processed_values = {}
        for reg_name, config in self.model_config.register_map.items():
            addr = config.address
            if addr == 0 or addr is None:
                continue
            raw_value = reg_to_value.get(addr)
            if raw_value is not None:
                processed_values[reg_name] = self.model_config.process_value(reg_name, raw_value)
            else:
                logger.warning(f"No value for register {reg_name} at address {addr}")
        
        logger.debug(f"Processed values: {processed_values}")
        
        # Create data objects
        battery = self._create_battery_data(processed_values)
        pv = self._create_pv_data(processed_values)
        grid = self._create_grid_data(processed_values)
        output = self._create_output_data(processed_values)
        status = self._create_system_status(processed_values)
        
        return battery, pv, grid, output, status
        
    def _create_battery_data(self, values: Dict[str, Any]) -> Optional[BatteryData]:
        """Create BatteryData object from processed values."""
        try:
            if any(key in values for key in ["battery_voltage", "battery_current", "battery_soc"]):
                return BatteryData(
                    voltage=values.get("battery_voltage"),
                    current=values.get("battery_current"),
                    power=values.get("battery_power"),
                    soc=values.get("battery_soc"),
                    temperature=values.get("battery_temperature")
                )
        except Exception as e:
            logger.warning(f"Failed to create BatteryData: {e}")
        return None
        
    def _create_pv_data(self, values: Dict[str, Any]) -> Optional[PVData]:
        """Create PVData object from processed values."""
        try:
            if any(key in values for key in ["pv_total_power", "pv_charging_power"]):
                return PVData(
                    total_power=values.get("pv_total_power"),
                    charging_power=values.get("pv_charging_power"),
                    charging_current=values.get("pv_charging_current"),
                    temperature=values.get("pv_temperature"),
                    pv1_voltage=values.get("pv1_voltage"),
                    pv1_current=values.get("pv1_current"),
                    pv1_power=values.get("pv1_power"),
                    pv2_voltage=values.get("pv2_voltage"),
                    pv2_current=values.get("pv2_current"),
                    pv2_power=values.get("pv2_power"),
                    pv_generated_today=values.get("pv_energy_today"),
                    pv_generated_total=values.get("pv_energy_total")
                )
        except Exception as e:
            logger.warning(f"Failed to create PVData: {e}")
        return None
        
    def _create_grid_data(self, values: Dict[str, Any]) -> Optional[GridData]:
        """Create GridData object from processed values."""
        try:
            if any(key in values for key in ["grid_voltage", "grid_power", "grid_frequency"]):
                return GridData(
                    voltage=values.get("grid_voltage"),
                    power=values.get("grid_power"),
                    frequency=values.get("grid_frequency")
                )
        except Exception as e:
            logger.warning(f"Failed to create GridData: {e}")
        return None
        
    def _create_output_data(self, values: Dict[str, Any]) -> Optional[OutputData]:
        """Create OutputData object from processed values."""
        try:
            if any(key in values for key in ["output_voltage", "output_power"]):
                return OutputData(
                    voltage=values.get("output_voltage"),
                    current=values.get("output_current"),
                    power=values.get("output_power"),
                    apparent_power=values.get("output_apparent_power"),
                    load_percentage=values.get("output_load_percentage"),
                    frequency=values.get("output_frequency")
                )
        except Exception as e:
            logger.warning(f"Failed to create OutputData: {e}")
        return None
        
    def _create_system_status(self, values: Dict[str, Any]) -> Optional[SystemStatus]:
        """Create SystemStatus object from processed values."""
        try:
            # Create timestamp if time registers are available
            inverter_timestamp = None
            if all(f"time_register_{i}" in values for i in range(6)):
                try:
                    year = values["time_register_0"]
                    month = values["time_register_1"]
                    day = values["time_register_2"]
                    hour = values["time_register_3"]
                    minute = values["time_register_4"]
                    second = values["time_register_5"]
                    inverter_timestamp = datetime.datetime(year, month, day, hour, minute, second)
                except Exception as e:
                    logger.warning(f"Failed to create timestamp: {e}")

            # Create operating mode
            if "operation_mode" in values:
                mode_value = values["operation_mode"]
                try:
                    op_mode = OperatingMode(mode_value)
                    return SystemStatus(
                        operating_mode=op_mode,
                        mode_name=op_mode.name,
                        inverter_time=inverter_timestamp
                    )
                except ValueError:
                    return SystemStatus(
                        operating_mode=OperatingMode.FAULT,
                        mode_name=f"UNKNOWN ({mode_value})",
                        inverter_time=inverter_timestamp
                    )
        except Exception as e:
            logger.warning(f"Failed to create SystemStatus: {e}")
        return None
