# async_isolar.py
import logging
from typing import Optional, Dict, Tuple, Any
from .async_modbusclient import AsyncModbusClient
from .modbusclient import (
    create_request,
    decode_modbus_response,
    create_ascii_request,
    decode_ascii_response,
)
from .isolar import BatteryData, PVData, GridData, OutputData, SystemStatus, OperatingMode
from .models import MODEL_CONFIGS, ModelConfig

_LOGGER = logging.getLogger(__name__)

class AsyncISolar:
    """Asynchronous interface to ISolar inverters (Modbus TCP or ASCII)."""

    def __init__(self, inverter_ip: str, local_ip: str, model: str = "ISOLAR_SMG_II_11K"):
        port = 502 if model == "VOLTRONIC_ASCII" else 8899
        self.client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip, port=port)
        self._transaction_id = 0x0772

        if model not in MODEL_CONFIGS:
            raise ValueError(f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}")
        self.model = model
        self.model_config: ModelConfig = MODEL_CONFIGS[model]
        _LOGGER.info(f"AsyncISolar initialized with model: {model} on port {port}")

    def _get_next_transaction_id(self) -> int:
        tid = self._transaction_id
        self._transaction_id = (tid + 1) & 0xFFFF
        return tid

    async def _read_registers_bulk(
        self,
        register_groups: list[tuple[int, int]],
        data_format: str = "Int",
    ) -> list[Optional[list[int]]]:
        """Read multiple groups of registers in a single connection."""
        try:
            requests = [
                create_request(self._get_next_transaction_id(), 0x0001, 0x00, 0x03, start, count)
                for start, count in register_groups
            ]
            _LOGGER.debug(f"Sending bulk request for register groups: {register_groups}")
            responses = await self.client.send_bulk(requests)

            decoded_groups: list[Optional[list[int]]] = [None] * len(register_groups)
            for i, (response, (_, count)) in enumerate(zip(responses, register_groups)):
                try:
                    if response:
                        out = decode_modbus_response(response, count, data_format)
                        _LOGGER.debug(f"Decoded values for group {i}: {out}")
                        decoded_groups[i] = out
                    else:
                        _LOGGER.warning(f"No response for register group {register_groups[i]}")
                except Exception as e:
                    _LOGGER.warning(f"Failed to decode group {register_groups[i]}: {e}")
            return decoded_groups
        except Exception as e:
            _LOGGER.error(f"Error reading registers bulk: {e}")
            return [None] * len(register_groups)

    # ------------------ ASCII branch helpers ------------------

    def _parse_qpiws(self, raw: str) -> str:
        """Parse QPIWS warnings bitstring into a readable list."""
        bits = list(raw.lstrip("(").strip())
        messages = []
        # Map index -> message (subset commonly exposed by Voltronic)
        bit_map = {
            1: "Inverter fault",
            2: "Bus over",
            3: "Bus under",
            4: "Bus soft fail",
            5: "Line fail",
            6: "OPV short",
            7: "Inverter voltage low",
            8: "Inverter voltage high",
            9: "Inverter soft fail",
            10: "Inverter over current",
            11: "Inverter over load",
            12: "Inverter over temperature",
            13: "Fan locked",
            14: "Battery voltage high",
            15: "Battery low alarm",
            17: "Battery under shutdown",
            19: "Over load",
            20: "EEPROM fault",
            21: "Inverter over current",
            22: "Inverter soft fail",
            23: "Self test fail",
            24: "OP DC voltage over",
            25: "Battery open",
            26: "Current sensor fail",
            27: "Battery short",
            28: "Power limit",
            29: "PV voltage high",
            30: "MPPT overload fault",
            31: "MPPT overload warning",
            32: "Battery too low to charge",
        }
        for idx, label in bit_map.items():
            if idx < len(bits) and bits[idx] == "1":
                messages.append(label)
        return ", ".join(messages) if messages else "No warnings"

    def _parse_qpiri_output_mode(self, field: str) -> str:
        """Decode QPIRI Output Mode field (21)."""
        mapping = {
            "00": "Single machine output",
            "01": "Parallel output",
            "02": "Phase 1 of 3‑phase output",
            "03": "Phase 2 of 3‑phase output",
            "04": "Phase 3 of 3‑phase output",
            "05": "Phase 1 of 2‑phase output",
            "06": "Phase 2 of 2‑phase output (120°)",
            "07": "Phase 2 of 2‑phase output (180°)",
        }
        key = field if len(field) == 2 else field.zfill(2)
        return mapping.get(key, f"Unknown ({field})")

    async def _get_all_data_ascii(
        self,
    ) -> Tuple[
        Optional[BatteryData],
        Optional[PVData],
        Optional[GridData],
        Optional[OutputData],
        Optional[SystemStatus],
    ]:
        """Fetch and parse ASCII data."""
        # Add QPIWS and QPIRI
        commands = ["QPIGS", "QPIGS2", "QMOD", "QPIWS", "QPIRI"]
        requests = [create_ascii_request(self._get_next_transaction_id(), 0x0001, cmd) for cmd in commands]
        responses = await self.client.send_bulk(requests)
        if not responses or len(responses) < 3:
            raise ValueError("Failed to get ASCII responses")

        raw_qpigs  = decode_ascii_response(responses[0])
        raw_qpigs2 = decode_ascii_response(responses[1]) if len(responses) > 1 else ""
        raw_qmod   = decode_ascii_response(responses[2]) if len(responses) > 2 else ""
        raw_qpiws  = decode_ascii_response(responses[3]) if len(responses) > 3 else ""
        raw_qpiri  = decode_ascii_response(responses[4]) if len(responses) > 4 else ""

        if not raw_qpigs or not raw_qmod:
            raise ValueError("Invalid ASCII responses")

        qpigs  = raw_qpigs.lstrip("(").split()
        qpigs2 = raw_qpigs2.lstrip("(").split() if raw_qpigs2 else []
        qmod   = raw_qmod.lstrip("(").strip()
        qpiri  = raw_qpiri.lstrip("(").split() if raw_qpiri else []

        if len(qpigs) < 21:
            raise ValueError("Unexpected QPIGS format")

        vals: Dict[str, Any] = {}

        # 1) Grid & Output (QPIGS)
        vals["grid_voltage"]          = float(qpigs[0])
        vals["grid_frequency"]        = float(qpigs[1]) * 100
        vals["output_voltage"]        = float(qpigs[2])
        vals["output_frequency"]      = float(qpigs[3]) * 100
        vals["output_apparent_power"] = int(float(qpigs[4]))
        vals["output_power"]          = int(float(qpigs[5]))
        vals["output_load_percentage"]= int(float(qpigs[6]))

        # 2) Battery (QPIGS)
        vals["battery_voltage"]        = float(qpigs[8])
        battery_chg                    = float(qpigs[9])
        battery_dis                    = float(qpigs[15])
        vals["battery_charge_current"] = battery_chg
        vals["battery_discharge_current"] = battery_dis
        vals["battery_current"]        = battery_chg - battery_dis
        vals["battery_power"]          = int(vals["battery_voltage"] * vals["battery_current"])
        vals["battery_soc"]            = int(float(qpigs[10]))
        # inverter heat‑sink temperature (not PV temp)
        vals["battery_temperature"]    = int(float(qpigs[11]))
        # SCC battery voltage (QPIGS[14])
        vals["battery_voltage_scc"]    = float(qpigs[14])

        # 3) PV1 (QPIGS) + PV2 (QPIGS2)
        pv1_curr  = float(qpigs[12])
        pv1_volt  = float(qpigs[13])
        raw_pv1   = qpigs[19]
        pv1_power = int(float(raw_pv1) * (1000 if "." in raw_pv1 else 1))

        vals["pv_charging_current"] = pv1_curr
        vals["pv1_voltage"]         = pv1_volt
        vals["pv1_current"]         = pv1_curr
        vals["pv1_power"]           = pv1_power
        vals["pv_total_power"]      = pv1_power
        vals["pv_charging_power"]   = pv1_power

        if len(qpigs2) >= 3:
            pv2_curr  = float(qpigs2[0])
            pv2_volt  = float(qpigs2[1])
            raw_pv2   = qpigs2[2]
            pv2_power = int(float(raw_pv2) * (1000 if "." in raw_pv2 else 1))
            vals["pv2_current"]       = pv2_curr
            vals["pv2_voltage"]       = pv2_volt
            vals["pv2_power"]         = pv2_power
            vals["pv_total_power"]   += pv2_power
            vals["pv_charging_power"]+= pv2_power
            vals["pv_charging_current"] += pv2_curr
        else:
            vals["pv2_current"]       = 0.0
            vals["pv2_voltage"]       = 0.0
            vals["pv2_power"]         = 0

        # 4) Grid import estimate
        net = vals["output_power"] + vals["battery_power"] - vals["pv_charging_power"]
        vals["grid_power"] = max(0, net)

        # 5) Output current (derived)
        if vals["output_voltage"] > 0:
            vals["output_current"] = round(vals["output_apparent_power"] / vals["output_voltage"], 1)
        else:
            vals["output_current"] = None

        # 6) Device status flags (raw strings from QPIGS)
        vals["device_status"]  = qpigs[16]
        vals["device_status2"] = qpigs[20]

        # 7) Energy stats: not provided by ASCII
        vals["pv_energy_today"] = None
        vals["pv_energy_total"] = None

        # 8) System operating mode (QMOD)
        if qmod in ["L", "C"]:
            vals["operation_mode"] = 2
            vals["mode_name"] = "Line Mode" if qmod == "L" else "Charging Mode"
        elif qmod == "B":
            vals["operation_mode"] = 3
            vals["mode_name"] = "Battery Mode"
        else:
            vals["operation_mode"] = 0
            vals["mode_name"] = f"UNKNOWN ({qmod})"
        vals["inverter_time"] = None

        # 9) Warnings (QPIWS)
        if raw_qpiws:
            vals["warnings"] = self._parse_qpiws(raw_qpiws)

        # 10) Output mode from QPIRI
        if qpiri and len(qpiri) >= 22:
            vals["output_mode_qpiri"] = self._parse_qpiri_output_mode(qpiri[21])

        # Build dataclasses
        battery = self._create_battery_data(vals)
        pv      = self._create_pv_data(vals)
        grid    = self._create_grid_data(vals)
        output  = self._create_output_data(vals)
        system  = self._create_system_status(vals)
        return battery, pv, grid, output, system

    # ------------------ Public entry point ------------------

    async def get_all_data(
        self,
    ) -> Tuple[
        Optional[BatteryData],
        Optional[PVData],
        Optional[GridData],
        Optional[OutputData],
        Optional[SystemStatus],
    ]:
        """Route to ASCII or Modbus branch."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()

        # ---- Modbus branch (unchanged) ----
        register_groups = []
        current_group = None

        registers = sorted(set(
            self.model_config.get_address(name)
            for name in self.model_config.register_map.keys()
            if self.model_config.get_address(name) not in (None, 0)
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
            _LOGGER.warning("No registers defined for model")
            return None, None, None, None, None

        _LOGGER.debug(f"Optimized register groups: {register_groups}")
        decoded_groups = await self._read_registers_bulk(register_groups)
        all_values = []
        for group in decoded_groups:
            if group is not None:
                all_values.extend(group)

        reg_to_value = {}
        current_reg = register_groups[0][0]
        for value in all_values:
            reg_to_value[current_reg] = value
            current_reg += 1

        processed_values = {}
        for reg_name, config in self.model_config.register_map.items():
            addr = config.address
            if addr and addr in reg_to_value:
                raw_value = reg_to_value[addr]
                processed_values[reg_name] = (
                    config.processor(raw_value)
                    if config.processor
                    else raw_value * config.scale_factor
                )
        _LOGGER.debug(f"Processed values: {processed_values}")

        battery = self._create_battery_data(processed_values)
        pv      = self._create_pv_data(processed_values)
        grid    = self._create_grid_data(processed_values)
        output  = self._create_output_data(processed_values)
        status  = self._create_system_status(processed_values)
        return battery, pv, grid, output, status

    # ------------------ Dataclass builders ------------------

    def _create_battery_data(self, values: Dict[str, Any]) -> Optional[BatteryData]:
        try:
            if any(k in values for k in ["battery_voltage", "battery_current", "battery_soc"]):
                return BatteryData(
                    voltage=values.get("battery_voltage"),
                    current=values.get("battery_current"),
                    power=values.get("battery_power"),
                    soc=values.get("battery_soc"),
                    temperature=values.get("battery_temperature"),
                    charge_current=values.get("battery_charge_current"),
                    discharge_current=values.get("battery_discharge_current"),
                    voltage_scc=values.get("battery_voltage_scc"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create BatteryData: {e}")
        return None

    def _create_pv_data(self, values: Dict[str, Any]) -> Optional[PVData]:
        try:
            if any(k in values for k in ["pv_total_power", "pv_charging_power"]):
                return PVData(
                    total_power=values.get("pv_total_power"),
                    charging_power=values.get("pv_charging_power"),
                    charging_current=values.get("pv_charging_current"),
                    # For VOLTRONIC_ASCII we do not expose PV temperature (duplicate of heatsink)
                    temperature=None if self.model == "VOLTRONIC_ASCII" else values.get("pv_temperature"),
                    pv1_voltage=values.get("pv1_voltage"),
                    pv1_current=values.get("pv1_current"),
                    pv1_power=values.get("pv1_power"),
                    pv2_voltage=values.get("pv2_voltage"),
                    pv2_current=values.get("pv2_current"),
                    pv2_power=values.get("pv2_power"),
                    pv_generated_today=values.get("pv_energy_today"),
                    pv_generated_total=values.get("pv_energy_total"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create PVData: {e}")
        return None

    def _create_grid_data(self, values: Dict[str, Any]) -> Optional[GridData]:
        try:
            if any(k in values for k in ["grid_voltage", "grid_power", "grid_frequency"]):
                return GridData(
                    voltage=values.get("grid_voltage"),
                    power=values.get("grid_power"),
                    frequency=values.get("grid_frequency"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create GridData: {e}")
        return None

    def _create_output_data(self, values: Dict[str, Any]) -> Optional[OutputData]:
        try:
            if any(k in values for k in ["output_voltage", "output_power", "output_apparent_power"]):
                return OutputData(
                    voltage=values.get("output_voltage"),
                    current=values.get("output_current"),
                    power=values.get("output_power"),
                    apparent_power=values.get("output_apparent_power"),
                    load_percentage=values.get("output_load_percentage"),
                    frequency=values.get("output_frequency"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create OutputData: {e}")
        return None

    def _create_system_status(self, values: Dict[str, Any]) -> Optional[SystemStatus]:
        try:
            inverter_timestamp = values.get("inverter_time")
            if "operation_mode" in values:
                mode_value = values.get("operation_mode")
                try:
                    op_mode = OperatingMode(mode_value)
                except ValueError:
                    op_mode = OperatingMode.FAULT
                    values["mode_name"] = values.get("mode_name") or f"UNKNOWN ({mode_value})"
                return SystemStatus(
                    operating_mode=op_mode,
                    mode_name=values.get("mode_name"),
                    inverter_time=inverter_timestamp,
                    output_mode_qpiri=values.get("output_mode_qpiri"),
                    device_status=values.get("device_status"),
                    device_status2=values.get("device_status2"),
                    warnings=values.get("warnings"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create SystemStatus: {e}")
        return None
