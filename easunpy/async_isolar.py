"""
easunpy.async_isolar
--------------------
Async high-level API for Easun/Voltronic inverters.

- Constructor stays compatible with sensor.py:
    AsyncISolar(inverter_ip, local_ip, model="ISOLAR_SMG_II_11K")
- ASCII read path (QPIGS/QPIGS2/QMOD/QPIWS/QPIRI).
- Settings helpers (apply_setting + typed wrappers).
- IMPORTANT: Decoding is now robust to both bytes and hex-string inputs.
  If the underlying decode expects a hex str, we pass resp.hex() automatically.

Depends on:
- easunpy.async_modbusclient.AsyncModbusClient
- easunpy.modbusclient.create_ascii_request / decode_ascii_response
- easunpy.models dataclasses
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Tuple, Any, List

from .async_modbusclient import AsyncModbusClient
from .modbusclient import (
    create_ascii_request,
    decode_ascii_response,
)
from .models import (
    MODEL_CONFIGS,
    ModelConfig,
    BatteryData,
    PVData,
    GridData,
    OutputData,
    SystemStatus,
    OperatingMode,
)

_LOGGER = logging.getLogger("easunpy.async_isolar")


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

    # ---------------- Internal decode helper (bytes OR hex string) ----------------

    @staticmethod
    def _safe_decode(resp: Optional[bytes | str]) -> Optional[str]:
        """Decode a full Modbus-FF04 ASCII response from either bytes or hex-string."""
        if resp is None:
            return None
        try:
            return decode_ascii_response(resp)
        except TypeError as e:
            # Handle "fromhex() argument must be str, not bytes" (underlying expects hex string)
            if "fromhex" in str(e):
                if isinstance(resp, (bytes, bytearray, memoryview)):
                    return decode_ascii_response(bytes(resp).hex())
            raise

    # ---------------------------------------------------------------------
    # READ PATH (QPIGS/QPIGS2/QMOD/QPIWS/QPIRI) with graceful no-connection
    # ---------------------------------------------------------------------

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
        # If listener is up but inverter not connected yet, send_bulk returns [None, ...].
        if not responses or all(r is None for r in responses):
            _LOGGER.debug("No ASCII responses (likely not connected); skipping parse")
            return None, None, None, None, None

        # Decode (each decode returns a raw "(...)" string or None)
        raw_qpigs = self._safe_decode(responses[0])
        raw_qpigs2 = self._safe_decode(responses[1])
        raw_qmod = self._safe_decode(responses[2])
        raw_qpiws = self._safe_decode(responses[3])
        raw_qpiri = self._safe_decode(responses[4])

        # If the inverter sent nothing useful yet, skip gracefully.
        if not raw_qpigs or not raw_qmod:
            _LOGGER.debug("ASCII responses incomplete (no QPIGS/QMOD); skipping parse")
            return None, None, None, None, None

        qpigs = raw_qpigs.lstrip("(").split()
        qpigs2 = raw_qpigs2.lstrip("(").split() if raw_qpigs2 else []
        qpiws = raw_qpiws.lstrip("(").strip() if raw_qpiws else ""
        qpiri = raw_qpiri.lstrip("(").split() if raw_qpiri else []
        qmod = raw_qmod.lstrip("(").strip()

        if len(qpigs) < 21:
            _LOGGER.debug("Unexpected QPIGS format; skipping parse")
            return None, None, None, None, None

        vals: Dict[str, Any] = {}

        # -------- QPIGS (runtime) --------
        # Indices per your PS parser (21 fields):
        # 0 GV, 1 GF, 2 OV, 3 OF, 4 OVA, 5 OW, 6 OLOAD%, 7 BUSV,
        # 8 BV, 9 BchgA, 10 BCap%, 11 HeatSinkC,
        # 12 PV1A, 13 PV1V, 14 BattV_SCC, 15 BdisA,
        # 16 DevStatus, 17 FanOffset x10mV, 18 EEPROM, 19 PV1W, 20 DevStatus2
        vals["grid_voltage"] = float(qpigs[0])
        vals["grid_frequency"] = float(qpigs[1])
        vals["output_voltage"] = float(qpigs[2])
        vals["output_frequency"] = float(qpigs[3])
        vals["output_apparent_power"] = int(float(qpigs[4]))
        vals["output_power"] = int(float(qpigs[5]))
        vals["output_load_percentage"] = int(float(qpigs[6]))
        vals["bus_voltage"] = float(qpigs[7])

        vals["battery_voltage"] = float(qpigs[8])
        battery_chg = float(qpigs[9])                # A
        vals["battery_charge_current"] = battery_chg
        vals["battery_soc"] = int(float(qpigs[10]))  # %
        vals["battery_temperature"] = int(float(qpigs[11]))  # inverter heat-sink temp (°C)

        pv1_curr = float(qpigs[12])
        pv1_volt = float(qpigs[13])
        vals["battery_voltage_scc"] = float(qpigs[14])
        battery_dis = float(qpigs[15])
        vals["battery_discharge_current"] = battery_dis
        vals["device_status_flags"] = qpigs[16]
        vals["battery_voltage_offset_for_fans"] = float(qpigs[17])
        vals["eeprom_version"] = qpigs[18]

        raw_pv1_power = qpigs[19]
        pv1_power = int(float(raw_pv1_power))
        vals["device_status_flags2"] = qpigs[20]

        vals["battery_current"] = battery_chg - battery_dis
        vals["battery_power"] = int(vals["battery_voltage"] * vals["battery_current"])

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
            pv2_power = int(float(raw_pv2_power))
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

        # Grid power estimate: output + battery - pv
        net = vals["output_power"] + vals["battery_power"] - vals["pv_charging_power"]
        vals["grid_power"] = max(0, int(net))

        # Output current (A) ≈ S / V
        vals["output_current"] = round(
            (vals["output_apparent_power"] / vals["output_voltage"]) if vals["output_voltage"] > 0 else 0.0, 1
        )

        # -------- QPIWS (warnings) --------
        if qpiws:
            vals["warnings"] = self._decode_qpiws(qpiws)

        # -------- QMOD (operating mode) --------
        if qmod in ["L", "C"]:
            vals["operation_mode"] = OperatingMode.SUB.value
            vals["mode_name"] = "Line Mode" if qmod == "L" else "Charging Mode"
        elif qmod == "B":
            vals["operation_mode"] = OperatingMode.SBU.value
            vals["mode_name"] = "Battery Mode"
        elif qmod == "S":
            vals["operation_mode"] = OperatingMode.IDLE.value
            vals["mode_name"] = "Standby Mode"
        elif qmod == "F":
            vals["operation_mode"] = OperatingMode.FAULT.value
            vals["mode_name"] = "Fault Mode"
        else:
            vals["operation_mode"] = OperatingMode.FAULT.value
            vals["mode_name"] = f"UNKNOWN ({qmod})"

        # -------- QPIRI (ratings/settings) --------
        if qpiri:
            vals.update(self._map_qpiri(qpiri))

        # -------- Build dataclasses --------
        battery = BatteryData(
            voltage=vals.get("battery_voltage"),
            current=vals.get("battery_current"),
            power=vals.get("battery_power"),
            soc=vals.get("battery_soc"),
            temperature=vals.get("battery_temperature"),
            charge_current=vals.get("battery_charge_current"),
            discharge_current=vals.get("battery_discharge_current"),
            voltage_scc=vals.get("battery_voltage_scc"),
            voltage_offset_for_fans=vals.get("battery_voltage_offset_for_fans"),
            eeprom_version=vals.get("eeprom_version"),
        )
        pv = PVData(
            total_power=vals.get("pv_total_power"),
            charging_power=vals.get("pv_charging_power"),
            charging_current=vals.get("pv_charging_current"),
            temperature=None,  # PV temp duplicates inverter temp on this model
            pv1_voltage=vals.get("pv1_voltage"),
            pv1_current=vals.get("pv1_current"),
            pv1_power=vals.get("pv1_power"),
            pv2_voltage=vals.get("pv2_voltage"),
            pv2_current=vals.get("pv2_current"),
            pv2_power=vals.get("pv2_power"),
            pv_generated_today=None,
            pv_generated_total=None,
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
        system = SystemStatus(
            operating_mode=OperatingMode(vals.get("operation_mode", OperatingMode.FAULT.value)),
            mode_name=vals.get("mode_name"),
            inverter_time=None,
            warnings=vals.get("warnings"),
            device_status_flags=vals.get("device_status_flags"),
            device_status_flags2=vals.get("device_status_flags2"),
            bus_voltage=vals.get("bus_voltage"),
            grid_rating_voltage=vals.get("grid_rating_voltage"),
            grid_rating_current=vals.get("grid_rating_current"),
            ac_output_rating_voltage=vals.get("ac_output_rating_voltage"),
            ac_output_rating_frequency=vals.get("ac_output_rating_frequency"),
            ac_output_rating_current=vals.get("ac_output_rating_current"),
            ac_output_rating_apparent_power=vals.get("ac_output_rating_apparent_power"),
            ac_output_rating_active_power=vals.get("ac_output_rating_active_power"),
            battery_rating_voltage=vals.get("battery_rating_voltage"),
            battery_recharge_voltage=vals.get("battery_recharge_voltage"),
            battery_undervoltage=vals.get("battery_undervoltage"),
            battery_bulk_voltage=vals.get("battery_bulk_voltage"),
            battery_float_voltage=vals.get("battery_float_voltage"),
            battery_type=vals.get("battery_type"),
            max_ac_charging_current=vals.get("max_ac_charging_current"),
            max_charging_current=vals.get("max_charging_current"),
            input_voltage_range=vals.get("input_voltage_range"),
            output_source_priority=vals.get("output_source_priority"),
            charger_source_priority=vals.get("charger_source_priority"),
            parallel_max_num=vals.get("parallel_max_num"),
            machine_type=vals.get("machine_type"),
            topology=vals.get("topology"),
            output_mode_qpiri=vals.get("output_mode_qpiri"),
            battery_redischarge_voltage=vals.get("battery_redischarge_voltage"),
            pv_ok_condition=vals.get("pv_ok_condition"),
            pv_power_balance=vals.get("pv_power_balance"),
            max_charging_time_cv=vals.get("max_charging_time_cv"),
            max_discharging_current=vals.get("max_discharging_current"),
        )

        return battery, pv, grid, output, system

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
        # For VOLTRONIC_ASCII we only use ASCII branch.
        return await self._get_all_data_ascii()

    # ------------------ Helpers for parsing ------------------

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
    def _map_qpiri(fields: List[str]) -> Dict[str, Any]:
        """Map QPIRI numeric fields to typed values + labels."""
        def m_output_source(code: str) -> str:
            return {"0": "UtilitySolarBat", "1": "SolarUtilityBat", "2": "SolarBatUtility"}.get(code, code)
        def m_batt_type(code: str) -> str:
            return {"0": "AGM", "1": "Flooded", "2": "User", "3": "PYL"}.get(code, code)
        def m_machine_type(code: str) -> str:
            return {"00": "Grid tie", "01": "Off Grid", "10": "Hybrid"}.get(code, code)
        def m_charger_priority(code: str) -> str:
            return {"1": "Solar first", "2": "Solar + Utility", "3": "Only solar charging permitted"}.get(code, code)
        def m_output_mode(code: str) -> str:
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
        def m_topology(code: str) -> str:
            return {"0": "transformerless", "1": "transformer"}.get(code, code)
        def m_input_range(code: str) -> str:
            return {"0": "Appliance", "1": "UPS"}.get(code, code)
        def m_pv_ok(code: str) -> str:
            return {"0": "As long as one", "1": "All"}.get(code, code)
        def m_pv_balance(code: str) -> str:
            return {
                "0": "Max charging = PV input max",
                "1": "Max power = charging + utility",
            }.get(code, code)

        v: Dict[str, Any] = {}
        if len(fields) >= 27:
            v.update(
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
                    "battery_type": m_batt_type(fields[12]),
                    "max_ac_charging_current": float(fields[13]),
                    "max_charging_current": float(fields[14]),
                    "input_voltage_range": m_input_range(fields[15]),
                    "output_source_priority": m_output_source(fields[16]),
                    "charger_source_priority": m_charger_priority(fields[17]),
                    "parallel_max_num": int(float(fields[18])),
                    "machine_type": m_machine_type(fields[19]),
                    "topology": m_topology(fields[20]),
                    "output_mode_qpiri": m_output_mode(fields[21]),
                    "battery_redischarge_voltage": float(fields[22]),
                    "pv_ok_condition": m_pv_ok(fields[23]),
                    "pv_power_balance": m_pv_balance(fields[24]),
                    "max_charging_time_cv": int(float(fields[25])),
                    "max_discharging_current": float(fields[26]),
                }
            )
        return v

    # ---------------------------------------------------------------------
    # WRITE PATH (settings) — one-off ASCII commands with ACK/NAK check
    # ---------------------------------------------------------------------

    @staticmethod
    def _ack_ok(reply: str) -> bool:
        return reply.startswith("(ACK")

    async def apply_setting(self, command: str) -> bool:
        """
        Send a raw ASCII setting (e.g. POP00, PBFT54.0). Returns True on ACK.
        """
        packet = create_ascii_request(self._get_next_transaction_id(), 0x0001, command)
        resp = await self.client.send_ascii_command(packet)
        if not resp:
            return False
        payload = self._safe_decode(resp) or ""
        _LOGGER.debug("apply_setting(%s) -> %s", command, payload)
        return self._ack_ok(payload)

    # Convenience typed helpers (accept str or int where applicable)
    async def set_output_source_priority(self, mode: str | int) -> bool:
        mapping = {
            "utilitysolarb at": "00", "utility_solar_bat": "00", "utilitysolarbat": "00", 0: "00",
            "solar_utility_bat": "01", "solarutilitybat": "01", 1: "01",
            "solar_bat_utility": "02", "solarbatutility": "02", 2: "02",
        }
        k = str(mode).replace(" ", "").lower()
        if isinstance(mode, int):
            code = mapping.get(mode)
        else:
            code = mapping.get(k)
        if code is None:
            raise ValueError("Invalid output source priority")
        return await self.apply_setting(f"POP{code}")

    async def set_charger_source_priority(self, mode: str | int) -> bool:
        mapping = {"solarfirst": "01", 1: "01", "solarutility": "02", "solar_plus_utility": "02", 2: "02", "onlysolarcharging": "03", "only_solar": "03", 3: "03"}
        k = str(mode).replace(" ", "").lower()
        code = mapping.get(mode if isinstance(mode, int) else k)
        if code is None:
            raise ValueError("Invalid charger source priority")
        return await self.apply_setting(f"PCP{code}")

    async def set_grid_working_range(self, mode: str | int) -> bool:
        mapping = {"appliance": "00", 0: "00", "ups": "01", 1: "01"}
        k = str(mode).lower()
        code = mapping.get(mode if isinstance(mode, int) else k)
        if code is None:
            raise ValueError("Invalid grid working range")
        return await self.apply_setting(f"PGR{code}")

    async def set_battery_type(self, battery_type: str | int) -> bool:
        mapping = {"agm": "00", 0: "00", "flooded": "01", 1: "01", "user": "02", 2: "02", "pyl": "03", "pylontech": "03", 3: "03"}
        k = str(battery_type).lower()
        code = mapping.get(battery_type if isinstance(battery_type, int) else k)
        if code is None:
            raise ValueError("Invalid battery type")
        return await self.apply_setting(f"PBT{code}")

    async def set_output_mode(self, mode: str | int) -> bool:
        mapping = {"single": "00", 0: "00", "parallel": "01", 1: "01", "p1_3ph": "02", 2: "02", "p2_3ph": "03", 3: "03", "p3_3ph": "04", 4: "04", "p1_2ph": "05", 5: "05", "p2_2ph_120": "06", 6: "06", "p2_2ph_180": "07", 7: "07"}
        k = str(mode).lower()
        code = mapping.get(mode if isinstance(mode, int) else k)
        if code is None:
            raise ValueError("Invalid output mode")
        return await self.apply_setting(f"POPM{code}")

    async def set_rated_output_voltage(self, volts: int) -> bool:
        v = int(volts)
        if v not in (110, 120, 127, 220, 230, 240):
            raise ValueError("Output voltage must be one of 110,120,127,220,230,240")
        return await self.apply_setting(f"V{v:03d}")

    async def set_rated_output_frequency(self, hz: int | str) -> bool:
        v = int(hz)
        if v not in (50, 60):
            raise ValueError("Output frequency must be 50 or 60")
        return await self.apply_setting(f"F{v:02d}")

    async def set_max_charging_current(self, amps: int, parallel_id: int = 0) -> bool:
        a = int(amps)
        if not (0 <= a <= 150):
            raise ValueError("Max charging current must be 0..150 A")
        m = int(parallel_id)
        if not (0 <= m <= 9):
            raise ValueError("Parallel id must be 0..9")
        return await self.apply_setting(f"MNCHGC{m:d}{a:03d}")

    async def set_max_utility_charging_current(self, amps: int, parallel_id: int = 0) -> bool:
        a = int(amps)
        if not (0 <= a <= 30):
            raise ValueError("Utility max charging current must be 0..30 A")
        m = int(parallel_id)
        if not (0 <= m <= 9):
            raise ValueError("Parallel id must be 0..9")
        return await self.apply_setting(f"MUCHGC{m:d}{a:02d}")

    async def set_battery_recharge_voltage(self, volts: float) -> bool:
        v = float(volts)
        if not (22.0 <= v <= 62.0):
            raise ValueError("Recharge voltage out of safe range (24V:22.0–28.5 / 48V:44.0–51.0 typical)")
        return await self.apply_setting(f"PBCV{v:04.1f}")

    async def set_battery_redischarge_voltage(self, volts: float) -> bool:
        v = float(volts)
        if v == 0.0:
            return await self.apply_setting("PBDV00.0")
        if not (24.0 <= v <= 62.0):
            raise ValueError("Re-discharge voltage must be 24.0–62.0 V or 0.0")
        return await self.apply_setting(f"PBDV{v:04.1f}")

    async def set_battery_cutoff_voltage(self, volts: float) -> bool:
        v = float(volts)
        if not (36.0 <= v <= 48.0):
            raise ValueError("Cut-off voltage must be ~42.0–48.0 V for 48V systems")
        return await self.apply_setting(f"PSDV{v:04.1f}")

    async def set_battery_bulk_voltage(self, volts: float) -> bool:
        v = float(volts)
        if v == 0.0:
            return await self.apply_setting("PCVV00.0")
        if not (24.0 <= v <= 64.0):
            raise ValueError("Bulk/CV voltage must be 24.0–64.0 or 0.0")
        return await self.apply_setting(f"PCVV{v:04.1f}")

    async def set_battery_float_voltage(self, volts: float) -> bool:
        v = float(volts)
        if v == 0.0:
            return await self.apply_setting("PBFT00.0")
        if not (24.0 <= v <= 64.0):
            raise ValueError("Float voltage must be 24.0–64.0 or 0.0")
        return await self.apply_setting(f"PBFT{v:04.1f}")

    async def set_cv_stage_max_time(self, minutes: int) -> bool:
        m = int(minutes)
        if not (0 <= m <= 900) or m % 5 != 0:
            raise ValueError("CV stage max time must be 0..900 in steps of 5")
        return await self.apply_setting(f"PCVT{m:03d}")

    async def set_datetime(self) -> bool:
        from datetime import datetime
        return await self.apply_setting(datetime.now().strftime("DAT%y%m%d%H%M%S"))

    async def reset_pv_load_energy(self) -> bool:
        return await self.apply_setting("RTEY")

    async def erase_data_log(self) -> bool:
        return await self.apply_setting("RTDL")

    async def equalization_enable(self, enabled: bool) -> bool:
        return await self.apply_setting(f"PBEQE{1 if enabled else 0}")

    async def equalization_set_time(self, minutes: int) -> bool:
        m = int(minutes)
        if not (5 <= m <= 900) or m % 5 != 0:
            raise ValueError("Equalization time must be 5..900, step 5")
        return await self.apply_setting(f"PBEQT{m:03d}")

    async def equalization_set_period(self, days: int) -> bool:
        d = int(days)
        if not (0 <= d <= 90):
            raise ValueError("Equalization period must be 0..90 days")
        return await self.apply_setting(f"PBEQP{d:03d}")

    async def equalization_set_voltage(self, volts: float) -> bool:
        v = float(volts)
        if not (24.0 <= v <= 64.0):
            raise ValueError("Equalization voltage must be 24.00–64.00 V")
        return await self.apply_setting(f"PBEQV{v:05.2f}")

    async def equalization_set_over_time(self, minutes: int) -> bool:
        m = int(minutes)
        if not (5 <= m <= 900) or m % 5 != 0:
            raise ValueError("Equalization overtime must be 5..900, step 5")
        return await self.apply_setting(f"PBEQOT{m:03d}")

    async def equalization_activate_now(self, active: bool) -> bool:
        return await self.apply_setting(f"PBEQA{1 if active else 0}")

    async def battery_control(self, discharge_full_on: int, discharge_on_standby_allowed: int, charge_full_on: int) -> bool:
        for x in (discharge_full_on, discharge_on_standby_allowed, charge_full_on):
            if x not in (0, 1):
                raise ValueError("PBATCD flags must be 0 or 1")
        return await self.apply_setting(f"PBATCD{discharge_full_on}{discharge_on_standby_allowed}{charge_full_on}")

    async def set_max_discharge_current(self, amps: int) -> bool:
        a = int(amps)
        if a == 0:
            return await self.apply_setting("PBATMAXDISC000")
        if not (30 <= a <= 150):
            raise ValueError("Max discharge current must be 30..150 A (or 0 to disable)")
        return await self.apply_setting(f"PBATMAXDISC{a:03d}")
