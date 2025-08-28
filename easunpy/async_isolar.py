import logging
from typing import Optional, Dict, Tuple, Any, List
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
    """Asynchronous interface to ISolar/Voltronic inverters (Modbus TCP or ASCII)."""

    def __init__(self, inverter_ip: str, local_ip: str, model: str = "ISOLAR_SMG_II_11K"):
        # ASCII models use port 502 (the inverter connects to us). Modbus WiFi uses 8899.
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

    # ------------- Modbus helpers -------------

    async def _read_registers_bulk(
        self,
        register_groups: List[Tuple[int, int]],
        data_format: str = "Int",
    ) -> List[Optional[List[int]]]:
        """Read multiple groups of registers in a single connection."""
        try:
            requests = [
                create_request(self._get_next_transaction_id(), 0x0001, 0x00, 0x03, start, count)
                for start, count in register_groups
            ]
            _LOGGER.debug(f"Sending bulk request for register groups: {register_groups}")
            responses = await self.client.send_bulk(requests)

            decoded_groups: List[Optional[List[int]]] = [None] * len(register_groups)
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

    # ------------- ASCII branch -------------

    @staticmethod
    def _decode_qpiws(bits: str) -> str:
        """Turn QPIWS bit string into a readable warning list."""
        labels = {
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
        warnings = []
        for i, b in enumerate(bits, start=0):
            if b == "1" and i in labels:
                warnings.append(labels[i])
        return "No warnings" if not warnings else ", ".join(warnings)

    @staticmethod
    def _map_qpiri(fields: list[str]) -> Dict[str, Any]:
        """Map QPIRI numeric fields to typed values + labels."""
        def map_output_source_priority(code: str) -> str:
            return {"0": "UtilitySolarBat", "1": "SolarUtilityBat", "2": "SolarBatUtility"}.get(code, code)

        def map_battery_type(code: str) -> str:
            return {"0": "AGM", "1": "Flooded", "2": "User", "3": "PYL"}.get(code, code)

        def map_machine_type(code: str) -> str:
            return {"00": "Grid tie", "01": "Off Grid", "10": "Hybrid"}.get(code, code)

        def map_charger_priority(code: str) -> str:
            return {"1": "Solar first", "2": "Solar + Utility", "3": "Only solar charging permitted"}.get(code, code)

        def map_output_mode(code: str) -> str:
            return {
                "00": "Single machine output",
                "01": "Parallel output",
                "02": "Phase 1 of 3 Phase output",
                "03": "Phase 2 of 3 Phase output",
                "04": "Phase 3 of 3 Phase output",
                "05": "Phase 1 of 2 Phase output",
                "06": "Phase 2 of 2 Phase output (120°)",
                "07": "Phase 2 of 2 Phase output (180°)",
            }.get(code, code)

        def map_topology(code: str) -> str:
            return {"0": "transformerless", "1": "transformer"}.get(code, code)

        def map_input_voltage_range(code: str) -> str:
            return {"0": "Appliance", "1": "UPS"}.get(code, code)

        def map_pv_ok(code: str) -> str:
            return {"0": "As long as one", "1": "All"}.get(code, code)

        def map_pv_balance(code: str) -> str:
            return {
                "0": "Max charging = PV input max",
                "1": "Max power = charging + utility",
            }.get(code, code)

        # Defensive: some firmwares append items; we only read the standard 27.
        vals: Dict[str, Any] = {}
        if len(fields) >= 27:
            vals.update(
                {
                    "grid_rating_voltage": float(fields[0]),
                    "grid_rating_current": float(fields[1]),
                    "ac_output_rating_voltage": float(fields[2]),
                    "ac_output_rating_frequency": float(fields[3]),
                    "ac_output_rating_current": float(fields[4]),
                    "ac_output_rating_apparent_power": int(float(fields[5])),
                    "ac_output_rating_active_power": int(float(fields[6])),
                    "battery_rating_voltage": float(fields[7]),
                    "battery_recharge_voltage": float(fields[8]),
                    "battery_undervoltage": float(fields[9]),
                    "battery_bulk_voltage": float(fields[10]),
                    "battery_float_voltage": float(fields[11]),
                    "battery_type": map_battery_type(fields[12]),
                    "max_ac_charging_current": float(fields[13]),
                    "max_charging_current": float(fields[14]),
                    "input_voltage_range": map_input_voltage_range(fields[15]),
                    "output_source_priority": map_output_source_priority(fields[16]),
                    "charger_source_priority": map_charger_priority(fields[17]),
                    "parallel_max_num": int(float(fields[18])),
                    "machine_type": map_machine_type(fields[19]),
                    "topology": map_topology(fields[20]),
                    "output_mode_qpiri": map_output_mode(fields[21]),
                    "battery_redischarge_voltage": float(fields[22]),
                    "pv_ok_condition": map_pv_ok(fields[23]),
                    "pv_power_balance": map_pv_balance(fields[24]),
                    "max_charging_time_cv": int(float(fields[25])),
                    "max_discharging_current": float(fields[26]),
                }
            )
        return vals

    async def _get_all_data_ascii(
        self,
    ) -> Tuple[
        Optional[BatteryData],
        Optional[PVData],
        Optional[GridData],
        Optional[OutputData],
        Optional[SystemStatus],
    ]:
        """Fetch and parse QPIGS/QPIGS2/QMOD/QPIWS/QPIRI ASCII data."""
        commands = ["QPIGS", "QPIGS2", "QMOD", "QPIWS", "QPIRI"]
        requests = [create_ascii_request(self._get_next_transaction_id(), 0x0001, cmd) for cmd in commands]
        responses = await self.client.send_bulk(requests)
        if not responses or len(responses) < len(commands):
            raise ValueError("Failed to get ASCII responses")

        raw_qpigs = decode_ascii_response(responses[0])
        raw_qpigs2 = decode_ascii_response(responses[1])
        raw_qmod = decode_ascii_response(responses[2])
        raw_qpiws = decode_ascii_response(responses[3])
        raw_qpiri = decode_ascii_response(responses[4])

        if not raw_qpigs or not raw_qmod:
            raise ValueError("Invalid ASCII responses")

        qpigs = raw_qpigs.lstrip("(").split()
        qpigs2 = raw_qpigs2.lstrip("(").split() if raw_qpigs2 else []
        qpiws = raw_qpiws.lstrip("(").strip() if raw_qpiws else ""
        qpiri = raw_qpiri.lstrip("(").split() if raw_qpiri else []
        qmod = raw_qmod.lstrip("(").strip()

        if len(qpigs) < 21:
            raise ValueError("Unexpected QPIGS format")

        vals: Dict[str, Any] = {}

        # -------- QPIGS (runtime) --------
        # 0..6 grid/output basic
        vals["grid_voltage"] = float(qpigs[0])
        vals["grid_frequency"] = float(qpigs[1]) * 100
        vals["output_voltage"] = float(qpigs[2])
        vals["output_frequency"] = float(qpigs[3]) * 100
        vals["output_apparent_power"] = int(float(qpigs[4]))
        vals["output_power"] = int(float(qpigs[5]))
        vals["output_load_percentage"] = int(float(qpigs[6]))

        # 7 bus voltage
        vals["bus_voltage"] = float(qpigs[7])

        # Battery metrics
        vals["battery_voltage"] = float(qpigs[8])
        battery_chg = float(qpigs[9])                # A
        vals["battery_charge_current"] = battery_chg
        vals["battery_soc"] = int(float(qpigs[10]))  # %
        # Inverter heat sink temp (treat as "inverter temp")
        vals["battery_temperature"] = int(float(qpigs[11]))
        # PV1 I/V (also SCC batt voltage)
        pv1_curr = float(qpigs[12])
        pv1_volt = float(qpigs[13])
        vals["battery_voltage_scc"] = float(qpigs[14])
        battery_dis = float(qpigs[15])
        vals["battery_discharge_current"] = battery_dis
        vals["device_status_flags"] = qpigs[16]
        vals["battery_voltage_offset_for_fans"] = float(qpigs[17])
        vals["eeprom_version"] = qpigs[18]
        # PV1 charge power (some firmwares return kW with a dot)
        raw_pv1_power = qpigs[19]
        pv1_power = int(float(raw_pv1_power) * 1000) if "." in raw_pv1_power else int(float(raw_pv1_power))
        vals["device_status_flags2"] = qpigs[20]

        # Battery combined values
        vals["battery_current"] = battery_chg - battery_dis
        vals["battery_power"] = int(vals["battery_voltage"] * vals["battery_current"])

        # PV aggregates
        vals["pv1_current"] = pv1_curr
        vals["pv1_voltage"] = pv1_volt
        vals["pv1_power"] = pv1_power
        vals["pv_total_power"] = pv1_power
        vals["pv_charging_power"] = pv1_power
        vals["pv_charging_current"] = pv1_curr

        # -------- QPIGS2 (PV2 if present) --------
        if len(qpigs2) >= 3:
            pv2_curr = float(qpigs2[0])
            pv2_volt = float(qpigs2[1])
            raw_pv2_power = qpigs2[2]
            pv2_power = int(float(raw_pv2_power) * 1000) if "." in raw_pv2_power else int(float(raw_pv2_power))
            vals["pv2_current"] = pv2_curr
            vals["pv2_voltage"] = pv2_volt
            vals["pv2_power"] = pv2_power
            vals["pv_total_power"] += pv2_power
            vals["pv_charging_power"] += pv2_power
            vals["pv_charging_current"] += pv2_curr
        else:
            vals["pv2_current"] = 0.0
            vals["pv2_voltage"] = 0.0
            vals["pv2_power"] = 0

        # Grid import only (simple estimate)
        net = vals["output_power"] + vals["battery_power"] - vals["pv_charging_power"]
        vals["grid_power"] = max(0, int(net))

        # Output current (A) ≈ S / V
        try:
            vals["output_current"] = round(
                (vals["output_apparent_power"] / vals["output_voltage"]) if vals["output_voltage"] > 0 else 0.0, 1
            )
        except Exception:
            vals["output_current"] = None

        # No ASCII energy counters
        vals["pv_energy_today"] = None
        vals["pv_energy_total"] = None

        # -------- QPIWS (warnings) --------
        if qpiws:
            bits = qpiws.strip()
            vals["warnings"] = self._decode_qpiws(bits)

        # -------- QMOD (operating mode) --------
        if qmod in ["L", "C"]:
            vals["operation_mode"] = OperatingMode.SUB.value
            vals["mode_name"] = "Line Mode" if qmod == "L" else "Charging Mode"
        elif qmod == "B":
            vals["operation_mode"] = OperatingMode.SBU.value
            vals["mode_name"] = "Battery Mode"
        else:
            vals["operation_mode"] = OperatingMode.FAULT.value
            vals["mode_name"] = f"UNKNOWN ({qmod})"

        # -------- QPIRI (ratings/settings) --------
        if qpiri:
            vals.update(self._map_qpiri(qpiri))

        # -------- Build dataclasses --------
        battery = self._create_battery_data(vals)
        pv = self._create_pv_data(vals)
        grid = self._create_grid_data(vals)
        output = self._create_output_data(vals)
        system = self._create_system_status(vals)
        return battery, pv, grid, output, system

    # ------------- Public entry point -------------

    async def get_all_data(
        self,
    ) -> Tuple[
        Optional[BatteryData],
        Optional[PVData],
        Optional[GridData],
        Optional[OutputData],
        Optional[SystemStatus],
    ]:
        """Fetch all data for current model."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()

        # Modbus branch (unchanged)
        register_groups: List[Tuple[int, int]] = []
        current_group: Optional[Tuple[int, int]] = None

        registers = sorted(
            set(
                self.model_config.get_address(name)
                for name in self.model_config.register_map.keys()
                if self.model_config.get_address(name) not in (None, 0)
            )
        )

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
        all_values: List[int] = []
        for group in decoded_groups:
            if group is not None:
                all_values.extend(group)

        reg_to_value: Dict[int, int] = {}
        current_reg = register_groups[0][0]
        for value in all_values:
            reg_to_value[current_reg] = value
            current_reg += 1

        processed_values: Dict[str, Any] = {}
        for reg_name, config in self.model_config.register_map.items():
            addr = config.address
            if addr and addr in reg_to_value:
                raw_value = reg_to_value[addr]
                processed_values[reg_name] = (
                    config.processor(raw_value) if config.processor else raw_value * config.scale_factor
                )
        _LOGGER.debug(f"Processed values: {processed_values}")

        battery = self._create_battery_data(processed_values)
        pv = self._create_pv_data(processed_values)
        grid = self._create_grid_data(processed_values)
        output = self._create_output_data(processed_values)
        status = self._create_system_status(processed_values)
        return battery, pv, grid, output, status

    # ------------- Builders -------------

    def _create_battery_data(self, values: Dict[str, Any]) -> Optional[BatteryData]:
        try:
            if any(key in values for key in ["battery_voltage", "battery_current", "battery_soc"]):
                return BatteryData(
                    voltage=values.get("battery_voltage"),
                    current=values.get("battery_current"),
                    power=values.get("battery_power"),
                    soc=values.get("battery_soc"),
                    temperature=values.get("battery_temperature"),
                    charge_current=values.get("battery_charge_current"),
                    discharge_current=values.get("battery_discharge_current"),
                    voltage_scc=values.get("battery_voltage_scc"),
                    voltage_offset_for_fans=values.get("battery_voltage_offset_for_fans"),
                    eeprom_version=values.get("eeprom_version"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create BatteryData: {e}")
        return None

    def _create_pv_data(self, values: Dict[str, Any]) -> Optional[PVData]:
        try:
            if any(key in values for key in ["pv_total_power", "pv_charging_power"]):
                return PVData(
                    total_power=values.get("pv_total_power"),
                    charging_power=values.get("pv_charging_power"),
                    charging_current=values.get("pv_charging_current"),
                    temperature=None,  # We no longer expose PV temperature separately
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
            if any(key in values for key in ["grid_voltage", "grid_power", "grid_frequency"]):
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
            if any(key in values for key in ["output_voltage", "output_power"]):
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
            if "operation_mode" in values:
                mode_value = int(values.get("operation_mode"))
                try:
                    op_mode = OperatingMode(mode_value)
                except ValueError:
                    op_mode = OperatingMode.FAULT

                return SystemStatus(
                    operating_mode=op_mode,
                    mode_name=values.get("mode_name"),
                    inverter_time=values.get("inverter_time"),
                    warnings=values.get("warnings"),
                    device_status_flags=values.get("device_status_flags"),
                    device_status_flags2=values.get("device_status_flags2"),
                    bus_voltage=values.get("bus_voltage"),
                    grid_rating_voltage=values.get("grid_rating_voltage"),
                    grid_rating_current=values.get("grid_rating_current"),
                    ac_output_rating_voltage=values.get("ac_output_rating_voltage"),
                    ac_output_rating_frequency=values.get("ac_output_rating_frequency"),
                    ac_output_rating_current=values.get("ac_output_rating_current"),
                    ac_output_rating_apparent_power=values.get("ac_output_rating_apparent_power"),
                    ac_output_rating_active_power=values.get("ac_output_rating_active_power"),
                    battery_rating_voltage=values.get("battery_rating_voltage"),
                    battery_recharge_voltage=values.get("battery_recharge_voltage"),
                    battery_undervoltage=values.get("battery_undervoltage"),
                    battery_bulk_voltage=values.get("battery_bulk_voltage"),
                    battery_float_voltage=values.get("battery_float_voltage"),
                    battery_type=values.get("battery_type"),
                    max_ac_charging_current=values.get("max_ac_charging_current"),
                    max_charging_current=values.get("max_charging_current"),
                    input_voltage_range=values.get("input_voltage_range"),
                    output_source_priority=values.get("output_source_priority"),
                    charger_source_priority=values.get("charger_source_priority"),
                    parallel_max_num=values.get("parallel_max_num"),
                    machine_type=values.get("machine_type"),
                    topology=values.get("topology"),
                    output_mode_qpiri=values.get("output_mode_qpiri"),
                    battery_redischarge_voltage=values.get("battery_redischarge_voltage"),
                    pv_ok_condition=values.get("pv_ok_condition"),
                    pv_power_balance=values.get("pv_power_balance"),
                    max_charging_time_cv=values.get("max_charging_time_cv"),
                    max_discharging_current=values.get("max_discharging_current"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create SystemStatus: {e}")
        return None
