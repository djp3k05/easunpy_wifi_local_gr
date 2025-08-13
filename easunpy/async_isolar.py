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
        # Voltronic ASCII talks over the Modbus framing on TCP/502 (cloud tunnel emulation),
        # other models use the local UDP discovery + TCP callback on 8899.
        port = 502 if model == "VOLTRONIC_ASCII" else 8899
        self.client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip, port=port)
        self._transaction_id = 0x0772

        if model not in MODEL_CONFIGS:
            raise ValueError(
                f"Unknown inverter model: {model}. Available models: {list(MODEL_CONFIGS.keys())}"
            )
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

    # -----------------------------
    # ASCII (Voltronic) data path
    # -----------------------------
    async def _get_all_data_ascii(
        self,
    ) -> Tuple[
        Optional[BatteryData],
        Optional[PVData],
        Optional[GridData],
        Optional[OutputData],
        Optional[SystemStatus],
    ]:
        """
        Fetch and parse QPIGS / QPIGS2 / QMOD / QPIRI / QPIWS ASCII data.

        Field positions for QPIGS (as per your PowerShell):
        0: Grid V, 1: Grid F, 2: Out V, 3: Out F, 4: Out VA, 5: Out W, 6: Load %,
        7: BUS V, 8: Batt V, 9: Batt charge A, 10: Batt %,
        11: Heatsink °C, 12: PV1 A, 13: PV1 V, 14: SCC Batt V, 15: Batt discharge A,
        16: Device Status bits, 17: Fan V offset, 18: EEPROM ver, 19: PV1 charge W, 20: Device Status2.
        """
        # 1) Send ASCII commands (QPIRI + QPIWS added)
        commands = ["QPIGS", "QPIGS2", "QMOD", "QPIRI", "QPIWS"]
        requests = [create_ascii_request(self._get_next_transaction_id(), 0x0001, cmd) for cmd in commands]
        responses = await self.client.send_bulk(requests)
        if not responses or len(responses) < 3:
            raise ValueError("Failed to get ASCII responses")

        raw_qpigs = decode_ascii_response(responses[0]) if len(responses) > 0 else ""
        raw_qpigs2 = decode_ascii_response(responses[1]) if len(responses) > 1 else ""
        raw_qmod = decode_ascii_response(responses[2]) if len(responses) > 2 else ""
        raw_qpiri = decode_ascii_response(responses[3]) if len(responses) > 3 else ""
        raw_qpiws = decode_ascii_response(responses[4]) if len(responses) > 4 else ""

        if not raw_qpigs or not raw_qmod:
            raise ValueError("Invalid ASCII responses")

        qpigs = raw_qpigs.lstrip("(").split()
        qpigs2 = raw_qpigs2.lstrip("(").split() if raw_qpigs2 else []
        qmod = raw_qmod.lstrip("(").strip()
        qpiri_fields = raw_qpiri.lstrip("(").split() if raw_qpiri else []
        qpiws_bits = raw_qpiws.lstrip("(").strip() if raw_qpiws else ""

        if len(qpigs) < 21:
            raise ValueError("Unexpected QPIGS format")

        vals: Dict[str, Any] = {}

        # ---- Grid & Output (instant) ----
        vals["grid_voltage"] = float(qpigs[0])
        vals["grid_frequency"] = float(qpigs[1]) * 100  # Hz *100
        vals["output_voltage"] = float(qpigs[2])
        vals["output_frequency"] = float(qpigs[3]) * 100  # Hz *100
        vals["output_apparent_power"] = int(float(qpigs[4]))
        vals["output_power"] = int(float(qpigs[5]))
        vals["output_load_percentage"] = int(float(qpigs[6]))

        # ---- Battery (instant) ----
        vals["battery_voltage"] = float(qpigs[8])
        batt_charge = float(qpigs[9])
        batt_discharge = float(qpigs[15])
        vals["battery_current"] = batt_charge - batt_discharge
        vals["battery_power"] = int(vals["battery_voltage"] * vals["battery_current"])
        vals["battery_soc"] = int(float(qpigs[10]))
        # Heatsink temperature is what the ASCII block exposes; we surface as "Inverter Temperature"
        vals["battery_temperature"] = int(float(qpigs[11]))

        # Extras from QPIGS (we'll attach them to sensors)
        vals["bus_voltage"] = float(qpigs[7])
        vals["scc_battery_voltage"] = float(qpigs[14])
        vals["battery_charge_current"] = batt_charge
        vals["battery_discharge_current"] = batt_discharge
        vals["eeprom_version"] = qpigs[18]
        vals["device_status"] = qpigs[16]
        vals["device_status_2"] = qpigs[20]

        # ---- PV1 ----
        pv1_curr = float(qpigs[12])
        pv1_volt = float(qpigs[13])
        raw_pv1 = qpigs[19]
        pv1_power = int(float(raw_pv1) * 1000) if "." in raw_pv1 else int(float(raw_pv1))

        vals["pv_charging_current"] = pv1_curr
        vals["pv1_voltage"] = pv1_volt
        vals["pv1_current"] = pv1_curr
        vals["pv1_power"] = pv1_power
        vals["pv_total_power"] = pv1_power
        vals["pv_charging_power"] = pv1_power
        vals["pv_temperature"] = vals["battery_temperature"]  # same probe in ASCII

        # ---- PV2 (if present) ----
        if len(qpigs2) >= 3:
            pv2_curr = float(qpigs2[0])
            pv2_volt = float(qpigs2[1])
            raw_pv2 = qpigs2[2]
            pv2_power = int(float(raw_pv2) * 1000) if "." in raw_pv2 else int(float(raw_pv2))
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

        # ---- Grid import (computed) ----
        net = vals["output_power"] + vals["battery_power"] - vals["pv_charging_power"]
        vals["grid_power"] = max(0, net)

        # ---- Energy (not provided by ASCII) ----
        vals["pv_energy_today"] = None
        vals["pv_energy_total"] = None

        # ---- QMOD -> human mode name + op code for HA enum ----
        if qmod in ["L", "C"]:
            vals["operation_mode"] = 2  # SUB
            vals["mode_name"] = "Line Mode" if qmod == "L" else "Charging Mode"
        elif qmod == "B":
            vals["operation_mode"] = 3  # SBU
            vals["mode_name"] = "Battery Mode"
        else:
            vals["operation_mode"] = 0
            vals["mode_name"] = f"UNKNOWN ({qmod})"
        vals["inverter_time"] = None

        # ---- QPIWS -> warnings text ----
        def _parse_qpiws(bits: str) -> str:
            if not bits:
                return ""
            warnings = []
            b = list(bits)
            # Use the mapping from your PowerShell
            labels = {
                0: "Reserved",
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
            for i, label in labels.items():
                if i < len(b) and b[i] == "1":
                    warnings.append(label)
            return ", ".join(warnings) if warnings else "No warnings"

        vals["warnings"] = _parse_qpiws(qpiws_bits)

        # ---- QPIRI -> ratings & settings ----
        def _safe_get(idx: int, conv=float, default=None):
            try:
                return conv(qpiri_fields[idx])
            except Exception:
                return default

        if qpiri_fields and len(qpiri_fields) >= 27:
            # Code->text mappings per your PowerShell
            def _map_output_source_priority(code: str) -> str:
                return {"0": "UtilitySolarBat", "1": "SolarUtilityBat", "2": "SolarBatUtility"}.get(code, code)

            def _map_battery_type(code: str) -> str:
                return {"0": "AGM", "1": "Flooded", "2": "User", "3": "PYL"}.get(code, code)

            def _map_machine_type(code: str) -> str:
                return {"00": "Grid tie", "01": "Off Grid", "10": "Hybrid"}.get(code, code)

            def _map_charger_source_priority(code: str) -> str:
                return {"1": "Solar first", "2": "Solar + Utility", "3": "Only solar charging permitted"}.get(code, code)

            def _map_output_mode(code: str) -> str:
                return {
                    "00": "single machine output",
                    "01": "parallel output",
                    "02": "Phase 1 of 3 Phase output",
                    "03": "Phase 2 of 3 Phase output",
                    "04": "Phase 3 of 3 Phase output",
                    "05": "Phase 1 of 2 Phase output",
                    "06": "Phase 2 of 2 Phase output (120°)",
                    "07": "Phase 2 of 2 Phase output (180°)",
                }.get(code, code)

            def _map_topology(code: str) -> str:
                return {"0": "transformerless", "1": "transformer"}.get(code, code)

            def _map_input_voltage_range(code: str) -> str:
                return {"0": "Appliance", "1": "UPS"}.get(code, code)

            # Ratings & settings (store into vals under 'system' later)
            vals["grid_rating_voltage"] = _safe_get(0, float)
            vals["grid_rating_current"] = _safe_get(1, float)
            vals["ac_output_rating_voltage"] = _safe_get(2, float)
            vals["ac_output_rating_frequency"] = _safe_get(3, float)
            vals["ac_output_rating_current"] = _safe_get(4, float)
            vals["ac_output_rating_apparent_power"] = _safe_get(5, int)
            vals["ac_output_rating_active_power"] = _safe_get(6, int)
            vals["battery_rating_voltage"] = _safe_get(7, float)
            vals["battery_recharge_voltage"] = _safe_get(8, float)
            vals["battery_under_voltage"] = _safe_get(9, float)
            vals["battery_bulk_voltage"] = _safe_get(10, float)
            vals["battery_float_voltage"] = _safe_get(11, float)
            vals["battery_type"] = _map_battery_type(qpiri_fields[12])
            vals["max_ac_charging_current"] = _safe_get(13, int)
            vals["max_charging_current"] = _safe_get(14, int)
            vals["input_voltage_range"] = _map_input_voltage_range(qpiri_fields[15])
            vals["output_source_priority"] = _map_output_source_priority(qpiri_fields[16])
            vals["charger_source_priority"] = _map_charger_source_priority(qpiri_fields[17])
            vals["parallel_max_num"] = _safe_get(18, int)
            vals["machine_type"] = _map_machine_type(qpiri_fields[19])
            vals["topology"] = _map_topology(qpiri_fields[20])
            vals["output_mode"] = _map_output_mode(qpiri_fields[21])
            vals["battery_re_discharge_voltage"] = _safe_get(22, float)
            vals["pv_ok_condition"] = qpiri_fields[23] if len(qpiri_fields) > 23 else None  # "0=As long as one,1=All"
            vals["pv_power_balance"] = qpiri_fields[24] if len(qpiri_fields) > 24 else None  # "0=Max charging,1=Max power"
            vals["max_charging_time_cv"] = _safe_get(25, int)
            vals["max_discharging_current"] = _safe_get(26, int)

        # ---- Build dataclasses ----
        battery = self._create_battery_data(vals)
        pv = self._create_pv_data(vals)
        grid = self._create_grid_data(vals)
        output = self._create_output_data(vals)
        system = self._create_system_status(vals)

        # Attach extra attributes so DataCollector exposes them as sensor data
        if battery:
            # QPIGS extras
            setattr(battery, "charge_current", vals.get("battery_charge_current"))
            setattr(battery, "discharge_current", vals.get("battery_discharge_current"))
            setattr(battery, "scc_voltage", vals.get("scc_battery_voltage"))
        if system:
            # QPIGS/QPIWS extras
            setattr(system, "bus_voltage", vals.get("bus_voltage"))
            setattr(system, "eeprom_version", vals.get("eeprom_version"))
            setattr(system, "device_status", vals.get("device_status"))
            setattr(system, "device_status_2", vals.get("device_status_2"))
            setattr(system, "warnings", vals.get("warnings"))

            # QPIRI ratings/settings
            for k in [
                "grid_rating_voltage",
                "grid_rating_current",
                "ac_output_rating_voltage",
                "ac_output_rating_frequency",
                "ac_output_rating_current",
                "ac_output_rating_apparent_power",
                "ac_output_rating_active_power",
                "battery_rating_voltage",
                "battery_recharge_voltage",
                "battery_under_voltage",
                "battery_bulk_voltage",
                "battery_float_voltage",
                "battery_type",
                "max_ac_charging_current",
                "max_charging_current",
                "input_voltage_range",
                "output_source_priority",
                "charger_source_priority",
                "parallel_max_num",
                "machine_type",
                "topology",
                "output_mode",
                "battery_re_discharge_voltage",
                "pv_ok_condition",
                "pv_power_balance",
                "max_charging_time_cv",
                "max_discharging_current",
            ]:
                setattr(system, k, vals.get(k))

        return battery, pv, grid, output, system

    # -----------------------------
    # Public API
    # -----------------------------
    async def get_all_data(
        self,
    ) -> Tuple[
        Optional[BatteryData],
        Optional[PVData],
        Optional[GridData],
        Optional[OutputData],
        Optional[SystemStatus],
    ]:
        """Get all inverter data, routing to ASCII or Modbus branch."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()

        # ---- Modbus branch (unchanged) ----
        register_groups = []
        current_group = None

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
                    config.processor(raw_value) if config.processor else raw_value * config.scale_factor
                )
        _LOGGER.debug(f"Processed values: {processed_values}")

        battery = self._create_battery_data(processed_values)
        pv = self._create_pv_data(processed_values)
        grid = self._create_grid_data(processed_values)
        output = self._create_output_data(processed_values)
        status = self._create_system_status(processed_values)
        return battery, pv, grid, output, status

    # -----------------------------
    # Builders
    # -----------------------------
    def _create_battery_data(self, values: Dict[str, Any]) -> Optional[BatteryData]:
        """Create BatteryData object from processed values."""
        try:
            if any(key in values for key in ["battery_voltage", "battery_current", "battery_soc"]):
                return BatteryData(
                    voltage=values.get("battery_voltage"),
                    current=values.get("battery_current"),
                    power=values.get("battery_power"),
                    soc=values.get("battery_soc"),
                    temperature=values.get("battery_temperature"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create BatteryData: {e}")
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
                    pv_generated_total=values.get("pv_energy_total"),
                )
        except Exception as e:
            _LOGGER.warning(f"Failed to create PVData: {e}")
        return None

    def _create_grid_data(self, values: Dict[str, Any]) -> Optional[GridData]:
        """Create GridData object from processed values."""
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
        """Create OutputData object from processed values."""
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
        """Create SystemStatus object from processed values."""
        try:
            inverter_timestamp = values.get("inverter_time") if "inverter_time" in values else None
            if "operation_mode" in values:
                mode_value = values.get("operation_mode")
                try:
                    op_mode = OperatingMode(mode_value)
                    return SystemStatus(
                        operating_mode=op_mode,
                        mode_name=values.get("mode_name"),
                        inverter_time=inverter_timestamp,
                    )
                except ValueError:
                    # Fallback to FAULT if enum doesn't recognize the code
                    return SystemStatus(
                        operating_mode=OperatingMode.FAULT,
                        mode_name=f"UNKNOWN ({mode_value})",
                        inverter_time=inverter_timestamp,
                    )
        except Exception as e:
            _LOGGER.warning(f"Failed to create SystemStatus: {e}")
        return None
