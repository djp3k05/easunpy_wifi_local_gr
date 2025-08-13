# async_isolar.py
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

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
        # Voltronic ASCII returns to TCP/502 after UDP "set>server="
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
        self, register_groups: List[Tuple[int, int]], data_format: str = "Int"
    ) -> List[Optional[List[int]]]:
        """Read multiple groups of registers in one connection (Modbus branch)."""
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
                        decoded_groups[i] = out
                    else:
                        _LOGGER.warning(f"No response for register group {register_groups[i]}")
                except Exception as e:
                    _LOGGER.warning(f"Failed to decode group {register_groups[i]}: {e}")
            return decoded_groups
        except Exception as e:
            _LOGGER.error(f"Error reading registers bulk: {e}")
            return [None] * len(register_groups)

    # ---------- ASCII helpers (Voltronic) ----------

    def _parse_qpiri(self, raw: str) -> Dict[str, Any]:
        """
        Parse QPIRI (ratings / settings). Field meaning follows your PowerShell. 
        """
        f = raw.lstrip("(").strip().split()
        if len(f) < 27:  # some models may have >27, we read the common subset
            _LOGGER.debug("QPIRI response shorter than expected")
        # Maps
        out_src_prio = {"0": "UtilitySolarBat", "1": "SolarUtilityBat", "2": "SolarBatUtility"}
        bat_type = {"0": "AGM", "1": "Flooded", "2": "User", "3": "PYL"}
        machine_type = {"00": "Grid tie", "01": "Off Grid", "10": "Hybrid"}
        chg_src_prio = {"1": "Solar first", "2": "Solar + Utility", "3": "Only solar charging"}
        output_mode = {
            "00": "single machine output",
            "01": "parallel output",
            "02": "Phase 1 of 3 Phase",
            "03": "Phase 2 of 3 Phase",
            "04": "Phase 3 of 3 Phase",
            "05": "Phase 1 of 2 Phase",
            "06": "Phase 2 of 2 Phase (120°)",
            "07": "Phase 2 of 2 Phase (180°)",
        }
        topology = {"0": "transformerless", "1": "transformer"}
        input_range = {"0": "Appliance", "1": "UPS"}

        def get(i, cast=float, default=None):
            try:
                return cast(f[i])
            except Exception:
                return default

        data: Dict[str, Any] = {
            "grid_rating_voltage": get(0, float),
            "grid_rating_current": get(1, float),
            "ac_output_rating_voltage": get(2, float),
            "ac_output_rating_frequency": get(3, float),
            "ac_output_rating_current": get(4, float),
            "ac_output_rating_apparent_power": get(5, int),
            "ac_output_rating_active_power": get(6, int),
            "battery_rating_voltage": get(7, float),
            "battery_recharge_voltage": get(8, float),
            "battery_under_voltage": get(9, float),
            "battery_bulk_voltage": get(10, float),
            "battery_float_voltage": get(11, float),
            "battery_type": bat_type.get(f[12], f[12] if len(f) > 12 else None),
            "max_ac_charging_current": get(13, int),
            "max_charging_current": get(14, int),
            "input_voltage_range": input_range.get(f[15], f[15] if len(f) > 15 else None),
            "output_source_priority": out_src_prio.get(f[16], f[16] if len(f) > 16 else None),
            "charger_source_priority": chg_src_prio.get(f[17], f[17] if len(f) > 17 else None),
            "parallel_max_num": get(18, int),
            "machine_type": machine_type.get(f[19], f[19] if len(f) > 19 else None),
            "topology": topology.get(f[20], f[20] if len(f) > 20 else None),
            "output_mode": output_mode.get(f[21], f[21] if len(f) > 21 else None),
            "battery_re_discharge_voltage": get(22, float),
            "pv_ok_condition": get(23, str),
            "pv_power_balance": get(24, str),
            "max_charging_time_cv": get(25, int),
            "max_discharging_current": get(26, int),
        }
        return data

    def _parse_qpiws(self, raw: str) -> str:
        """Parse QPIWS into a comma-separated warning text."""
        bits = list(raw.lstrip("(").strip())
        flags = []
        def on(i, text):
            if i < len(bits) and bits[i] == "1":
                flags.append(text)
        # subset based on your script
        on(1, "Inverter fault"); on(2, "Bus over"); on(3, "Bus under"); on(4, "Bus soft fail")
        on(5, "Line fail"); on(6, "OPV short"); on(7, "Inv voltage low"); on(8, "Inv voltage high")
        on(9, "Inv soft fail"); on(10, "Inv over current"); on(11, "Inv over load")
        on(12, "Inverter over temp"); on(13, "Fan locked"); on(14, "Battery voltage high")
        on(15, "Battery low alarm"); on(17, "Battery under shutdown"); on(19, "Over load")
        on(20, "EEPROM fault"); on(21, "Inv over current"); on(22, "Inv soft fail")
        on(23, "Self test fail"); on(24, "OP DC voltage over"); on(25, "Battery open")
        on(26, "Current sensor fail"); on(27, "Battery short"); on(28, "Power limit")
        on(29, "PV voltage high"); on(30, "MPPT overload fault"); on(31, "MPPT overload warn")
        if not flags:
            return "No warnings"
        return ", ".join(flags)

    async def _get_all_data_ascii(
        self,
    ) -> Tuple[Optional[BatteryData], Optional[PVData], Optional[GridData], Optional[OutputData], Optional[SystemStatus]]:
        """Fetch and parse QPIGS/QPIGS2/QMOD/QPIRI/QPIWS (ASCII)."""
        commands = ["QPIGS", "QPIGS2", "QMOD", "QPIRI", "QPIWS"]
        requests = [create_ascii_request(self._get_next_transaction_id(), 0x0001, c) for c in commands]
        responses = await self.client.send_bulk(requests)
        if not responses or len(responses) < 3:
            raise ValueError("Failed to get ASCII responses")

        raw_qpigs = decode_ascii_response(responses[0])
        raw_qpigs2 = decode_ascii_response(responses[1]) if len(responses) > 1 else ""
        raw_qmod = decode_ascii_response(responses[2])
        raw_qpiri = decode_ascii_response(responses[3]) if len(responses) > 3 else ""
        raw_qpiws = decode_ascii_response(responses[4]) if len(responses) > 4 else ""

        if not raw_qpigs or not raw_qmod:
            raise ValueError("Invalid ASCII responses")

        f = raw_qpigs.lstrip("(").split()
        f2 = raw_qpigs2.lstrip("(").split() if raw_qpigs2 else []

        if len(f) < 21:
            raise ValueError("Unexpected QPIGS format")

        vals: Dict[str, Any] = {}

        # Grid & Output
        vals["grid_voltage"] = float(f[0])
        vals["grid_frequency"] = float(f[1]) * 100
        vals["output_voltage"] = float(f[2])
        vals["output_frequency"] = float(f[3]) * 100
        vals["output_apparent_power"] = int(f[4])
        vals["output_power"] = int(f[5])
        vals["output_load_percentage"] = int(f[6])
        vals["bus_voltage"] = float(f[7])

        # Battery
        vals["battery_voltage"] = float(f[8])
        charge_a = float(f[9])
        vals["battery_soc"] = int(f[10])
        inv_temp = int(f[11])
        vals["battery_temperature"] = inv_temp          # using heatsink temp
        vals["pv_temperature"] = inv_temp               # same as original code
        pv1_current = float(f[12])
        pv1_voltage = float(f[13])
        vals["battery_voltage_from_scc"] = float(f[14])
        discharge_a = float(f[15])
        vals["battery_current"] = charge_a - discharge_a
        vals["battery_power"] = int(vals["battery_voltage"] * vals["battery_current"])

        # PV1 & PV charging
        raw_pv_power = f[19]
        pv1_power = int(float(raw_pv_power) * 1000) if "." in raw_pv_power else int(raw_pv_power)
        vals["pv1_voltage"] = pv1_voltage
        vals["pv1_current"] = pv1_current
        vals["pv1_power"] = pv1_power
        vals["pv_charging_current"] = pv1_current
        vals["pv_total_power"] = pv1_power
        vals["pv_charging_power"] = pv1_power

        # Extra status/info
        vals["device_status_flags"] = f[16]
        vals["battery_voltage_offset_fans"] = int(f[17])  # reported as x10mV by protocol
        vals["eeprom_version"] = f[18]
        vals["device_status_flags_2"] = f[20]

        # PV2 (QPIGS2)
        if len(f2) >= 3:
            pv2_curr = float(f2[0]); pv2_volt = float(f2[1])
            raw_pv2 = f2[2]
            pv2_power = int(float(raw_pv2) * 1000) if "." in raw_pv2 else int(raw_pv2)
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

        # Grid import approximation
        net = vals["output_power"] + vals["battery_power"] - vals["pv_charging_power"]
        vals["grid_power"] = max(0, int(net))

        # Energy (not available in ASCII)
        vals["pv_energy_today"] = None
        vals["pv_energy_total"] = None

        # Mode
        qmod = raw_qmod.lstrip("(").strip()
        if qmod in ["L", "C"]:
            vals["operation_mode"] = 2
            vals["mode_name"] = "Line Mode" if qmod == "L" else "Charging Mode"
        elif qmod == "B":
            vals["operation_mode"] = 3
            vals["mode_name"] = "Battery Mode"
        else:
            vals["operation_mode"] = 0
            vals["mode_name"] = f"UNKNOWN ({qmod})"

        # Ratings / warnings
        if raw_qpiri:
            vals.update(self._parse_qpiri(raw_qpiri))
        if raw_qpiws:
            vals["warnings_text"] = self._parse_qpiws(raw_qpiws)

        # Build dataclasses
        battery = self._create_battery_data(vals)
        pv = self._create_pv_data(vals)
        grid = self._create_grid_data(vals)
        output = self._create_output_data(vals)
        system = self._create_system_status(vals)
        return battery, pv, grid, output, system

    # ---------- Public API ----------

    async def get_all_data(
        self,
    ) -> Tuple[Optional[BatteryData], Optional[PVData], Optional[GridData], Optional[OutputData], Optional[SystemStatus]]:
        """Fetch all data for the selected model."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()

        # --- Modbus branch (unchanged design) ---
        registers = sorted(
            set(
                self.model_config.get_address(name)
                for name in self.model_config.register_map.keys()
                if self.model_config.get_address(name) not in (None, 0)
            )
        )
        if not registers:
            _LOGGER.warning("No registers defined for model")
            return None, None, None, None, None

        # Collapse contiguous ranges
        groups: List[Tuple[int, int]] = []
        start = None
        for r in registers:
            if start is None:
                start = (r, 1)
            elif r == start[0] + start[1]:
                start = (start[0], start[1] + 1)
            else:
                groups.append(start)
                start = (r, 1)
        if start:
            groups.append(start)

        decoded_groups = await self._read_registers_bulk(groups)
        all_values: List[int] = []
        for g in decoded_groups:
            if g is not None:
                all_values.extend(g)

        reg_to_val: Dict[int, int] = {}
        reg = groups[0][0]
        for v in all_values:
            reg_to_val[reg] = v
            reg += 1

        processed: Dict[str, Any] = {}
        for name, cfg in self.model_config.register_map.items():
            addr = cfg.address
            if addr and addr in reg_to_val:
                raw = reg_to_val[addr]
                processed[name] = cfg.processor(raw) if cfg.processor else raw * cfg.scale_factor

        battery = self._create_battery_data(processed)
        pv = self._create_pv_data(processed)
        grid = self._create_grid_data(processed)
        output = self._create_output_data(processed)
        status = self._create_system_status(processed)
        return battery, pv, grid, output, status

    # ---------- Dataclass builders ----------

    def _create_battery_data(self, values: Dict[str, Any]) -> Optional[BatteryData]:
        try:
            if any(k in values for k in ("battery_voltage", "battery_current", "battery_soc")):
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
        try:
            if any(k in values for k in ("pv_total_power", "pv_charging_power")):
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
        try:
            if any(k in values for k in ("grid_voltage", "grid_power", "grid_frequency")):
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
            if any(k in values for k in ("output_voltage", "output_power")):
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
            inv_time = values.get("inverter_time")
            if "operation_mode" in values:
                mv = values.get("operation_mode")
                try:
                    op = OperatingMode(mv)
                    status = SystemStatus(operating_mode=op, mode_name=values.get("mode_name"), inverter_time=inv_time)
                except ValueError:
                    # fall back to an UNKNOWN/FAULT-like status without breaking enum
                    status = SystemStatus(operating_mode=OperatingMode(values.get("operation_mode", 0)), mode_name=values.get("mode_name"), inverter_time=inv_time)

                # Attach any extra keys (QPIRI/QPIWS/QPIGS additions) so HA can expose them
                for k, v in values.items():
                    if k not in {"operation_mode", "mode_name", "inverter_time"}:
                        try:
                            setattr(status, k, v)
                        except Exception:
                            pass
                return status
        except Exception as e:
            _LOGGER.warning(f"Failed to create SystemStatus: {e}")
        return None
