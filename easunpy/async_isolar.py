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
    """Asynchronous interface to ISolar inverters (Modbus TCP or ASCII)."""

    def __init__(self, inverter_ip: str, local_ip: str, model: str = "ISOLAR_SMG_II_11K"):
        port = 502 if model == "VOLTRONIC_ASCII" else 8899
        self.client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip, port=port)
        self.model = model
        self.model_config: ModelConfig = MODEL_CONFIGS.get(model)  # type: ignore
        if not self.model_config:
            raise ValueError(f"Unknown model: {model}")
        logger.info(f"AsyncISolar initialized with model: {model} on port {port}")

    async def get_all_data(self) -> Tuple[BatteryData, PVData, GridData, OutputData, SystemStatus]:
        """Fetch all inverter data, either via ASCII or Modbus."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()
        return await self._get_all_data_modbus()

    async def _get_all_data_modbus(
        self,
    ) -> Tuple[BatteryData, PVData, GridData, OutputData, SystemStatus]:
        """Fetch data over Modbus registers."""
        cfg = self.model_config
        # Optimize register reads by grouping contiguous registers
        register_groups: List[Tuple[int, int]] = []
        current_group: Tuple[int, int] = (cfg.start_register, 0)
        for reg in cfg.registers:
            if current_group[1] == 0:
                current_group = (reg, 1)
            elif reg == current_group[0] + current_group[1]:
                current_group = (current_group[0], current_group[1] + 1)
            else:
                register_groups.append(current_group)
                current_group = (reg, 1)
        if current_group[1] > 0:
            register_groups.append(current_group)

        # Read & decode each group
        raw_results: Dict[int, List[int]] = {}
        for start, count in register_groups:
            request = create_request(self.client.unit_id, start, count)
            response = await self.client.send(request)
            values = decode_modbus_response(response, start, count)
            raw_results.update(values)

        # Map registers to values dict
        vals: Dict[str, Any] = {}
        for name, reg in cfg.register_map.items():
            vals[name] = raw_results.get(reg)

        # Build data classes
        battery = BatteryData(
            voltage=vals.get("battery_voltage"),
            current=vals.get("battery_current"),
            power=vals.get("battery_power"),
            soc=vals.get("battery_soc"),
            temperature=vals.get("battery_temperature"),
        )
        pv = PVData(
            total_power=vals.get("pv_total_power"),
            charging_power=vals.get("pv_charging_power"),
            charging_current=vals.get("pv_charging_current"),
            temperature=vals.get("pv_temperature"),
            pv1_voltage=vals.get("pv1_voltage"),
            pv1_current=vals.get("pv1_current"),
            pv1_power=vals.get("pv1_power"),
            pv2_voltage=vals.get("pv2_voltage"),
            pv2_current=vals.get("pv2_current"),
            pv2_power=vals.get("pv2_power"),
            energy_today=vals.get("pv_energy_today"),
            energy_total=vals.get("pv_energy_total"),
        )
        grid = GridData(
            voltage=vals.get("grid_voltage"),
            power=vals.get("grid_power"),
            frequency=vals.get("grid_frequency"),
        )
        output = OutputData(
            voltage=vals.get("output_voltage"),
            current=vals.get("output_current"),
            power=vals.get("output_power"),
            apparent_power=vals.get("output_apparent_power"),
            load_percentage=vals.get("output_load_percentage"),
            frequency=vals.get("output_frequency"),
        )

        mode_value = vals.get("system_mode")
        try:
            op_mode = OperatingMode(mode_value)
        except Exception:
            op_mode = OperatingMode.FAULT

        system = SystemStatus(
            operating_mode=op_mode,
            mode_name=op_mode.name,
            inverter_time=vals.get("inverter_time"),
        )

        return battery, pv, grid, output, system

    async def _get_all_data_ascii(
        self,
    ) -> Tuple[BatteryData, PVData, GridData, OutputData, SystemStatus]:
        """Fetch and parse QPIGS/QPIGS2 ASCII data from Voltronic inverters."""
        # Read primary status
        raw1 = await self.client.send_ascii("QPIGS")
        qpigs_parts = raw1.strip().split()
        # Read second PV string if available
        raw2 = await self.client.send_ascii("QPIGS2")
        qpigs2_parts = raw2.strip().split()

        # Parse basic fields
        values: Dict[str, Any] = {}
        # Battery
        values["battery_voltage"] = float(qpigs_parts[8])
        bat_charge = int(qpigs_parts[9])
        bat_discharge = int(qpigs_parts[15])
        values["battery_current"] = bat_charge - bat_discharge
        values["battery_power"] = int(values["battery_voltage"] * values["battery_current"])
        values["battery_soc"] = int(qpigs_parts[10])
        # Inverter heat-sink (formerly mislabeled Battery Temperature)
        values["battery_temperature"] = int(qpigs_parts[11])
        # Output
        values["output_voltage"] = float(qpigs_parts[2])
        values["output_current"] = float(qpigs_parts[3])
        values["output_power"] = int(qpigs_parts[5])
        values["output_apparent_power"] = int(qpigs_parts[6])
        values["output_load_percentage"] = int(qpigs_parts[7])
        values["output_frequency"] = float(qpigs_parts[4])
        # PV1
        values["pv_total_power"] = int(qpigs_parts[12])
        values["pv_charging_power"] = int(qpigs_parts[12])
        values["pv_charging_current"] = int(qpigs_parts[13])
        values["pv_temperature"] = int(qpigs_parts[14])
        values["pv1_voltage"] = float(qpigs_parts[12])  # reuse same for consistency
        values["pv1_current"] = float(qpigs_parts[13])
        values["pv1_power"] = int(qpigs_parts[12])
        # System
        mode_value = int(qpigs_parts[17])
        try:
            op_mode = OperatingMode(mode_value)
        except ValueError:
            op_mode = OperatingMode.FAULT
        inverter_timestamp = datetime.datetime.utcnow().isoformat()
        values["system_mode"] = mode_value
        # Grid frequency (not in QPIGS, reuse output freq)
        values["grid_frequency"] = values["output_frequency"]
        # Grid voltage not provided in ASCII
        values["grid_voltage"] = 0.0

        # PV2 (if present)
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
            values["pv_charging_current"] += pv2_current
        else:
            values["pv2_voltage"] = 0.0
            values["pv2_current"] = 0.0
            values["pv2_power"] = 0

        # Calculate net grid power after PV2 inclusion
        values["grid_power"] = (
            values["output_power"]
            + values["battery_power"]
            - values["pv_charging_power"]
        )

        # Build data classes
        battery = BatteryData(
            voltage=values["battery_voltage"],
            current=values["battery_current"],
            power=values["battery_power"],
            soc=values["battery_soc"],
            temperature=values["battery_temperature"],
        )
        pv = PVData(
            total_power=values["pv_total_power"],
            charging_power=values["pv_charging_power"],
            charging_current=values["pv_charging_current"],
            temperature=values["pv_temperature"],
            pv1_voltage=values["pv1_voltage"],
            pv1_current=values["pv1_current"],
            pv1_power=values["pv1_power"],
            pv2_voltage=values["pv2_voltage"],
            pv2_current=values["pv2_current"],
            pv2_power=values["pv2_power"],
            energy_today=0.0,
            energy_total=0.0,
        )
        grid = GridData(
            voltage=values["grid_voltage"],
            power=values["grid_power"],
            frequency=values["grid_frequency"],
        )
        output = OutputData(
            voltage=values["output_voltage"],
            current=values["output_current"],
            power=values["output_power"],
            apparent_power=values["output_apparent_power"],
            load_percentage=values["output_load_percentage"],
            frequency=values["output_frequency"],
        )
        system = SystemStatus(
            operating_mode=op_mode,
            mode_name=op_mode.name,
            inverter_time=inverter_timestamp,
        )

        return battery, pv, grid, output, system
