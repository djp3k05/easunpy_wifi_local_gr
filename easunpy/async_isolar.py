# async_isolar.py
import logging
from typing import Dict, Any, Tuple, Optional
from datetime import datetime as _dt

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
        # Port 502 for ASCII/Voltronic, 8899 for Modbus
        port = 502 if model == "VOLTRONIC_ASCII" else 8899
        self.client = AsyncModbusClient(inverter_ip=inverter_ip, local_ip=local_ip, port=port)
        self._transaction_id = 0x0772

        if model not in MODEL_CONFIGS:
            raise ValueError(f"Unknown inverter model: {model}")
        self.model = model
        self.model_config: ModelConfig = MODEL_CONFIGS[model]
        _LOGGER.info(f"AsyncISolar initialized with model: {model} on port {port}")

    def _next_transaction(self) -> int:
        tid = self._transaction_id
        self._transaction_id = (tid + 1) & 0xFFFF
        return tid

    async def get_all_data(self) -> Tuple[BatteryData, PVData, GridData, OutputData, SystemStatus]:
        """Fetch all inverter data, either via ASCII or Modbus."""
        if self.model == "VOLTRONIC_ASCII":
            return await self._get_all_data_ascii()
        return await self._get_all_data_modbus()

    async def _get_all_data_modbus(self) -> Tuple[BatteryData, PVData, GridData, OutputData, SystemStatus]:
        """Fetch data over Modbus registers (bulk reads)."""
        cfg = self.model_config
        # build contiguous register groups
        groups: list[tuple[int,int]] = []
        start: Optional[int] = None
        count = 0
        for reg in sorted(cfg.register_map.values(), key=lambda rc: rc.address):
            addr = reg.address
            if start is None:
                start, count = addr, 1
            elif addr == start + count:
                count += 1
            else:
                groups.append((start, count))
                start, count = addr, 1
        if start is not None:
            groups.append((start, count))

        # send bulk read
        requests = [
            create_request(self._next_transaction(), 0, 0x00, 0x03, s, c)
            for s, c in groups
        ]
        responses = await self.client.send_bulk(requests)
        raw: Dict[int,int] = {}
        for (s, c), resp in zip(groups, responses):
            if resp:
                decoded = decode_modbus_response(resp, s, c)
                raw.update(decoded)

        vals: Dict[str, Any] = {}
        for name, rc in self.model_config.register_map.items():
            raw_val = raw.get(rc.address)
            vals[name] = rc.processor(raw_val) if rc.processor else raw_val * rc.scale_factor

        # build dataclasses
        battery = BatteryData(
            voltage=vals["battery_voltage"],
            current=vals["battery_current"],
            power=vals["battery_power"],
            soc=vals["battery_soc"],
            temperature=vals["battery_temperature"],
        )
        pv = PVData(
            total_power=vals["pv_total_power"],
            charging_power=vals["pv_charging_power"],
            charging_current=vals["pv_charging_current"],
            temperature=vals["pv_temperature"],
            pv1_voltage=vals["pv1_voltage"],
            pv1_current=vals["pv1_current"],
            pv1_power=vals["pv1_power"],
            pv2_voltage=vals["pv2_voltage"],
            pv2_current=vals["pv2_current"],
            pv2_power=vals["pv2_power"],
            pv_generated_today=vals.get("pv_energy_today", 0),
            pv_generated_total=vals.get("pv_energy_total", 0),
        )
        grid = GridData(
            voltage=vals["grid_voltage"],
            power=vals["grid_power"],
            frequency=vals["grid_frequency"],
        )
        output = OutputData(
            voltage=vals["output_voltage"],
            current=vals["output_current"],
            power=vals["output_power"],
            apparent_power=vals["output_apparent_power"],
            load_percentage=vals["output_load_percentage"],
            frequency=vals["output_frequency"],
        )
        mode = vals.get("system_mode", 0)
        try:
            op = OperatingMode(mode)
        except ValueError:
            op = OperatingMode.FAULT
        system = SystemStatus(
            operating_mode=op,
            mode_name=op.name,
            inverter_time=vals.get("inverter_time"),
        )
        return battery, pv, grid, output, system

    async def _get_all_data_ascii(self) -> Tuple[BatteryData, PVData, GridData, OutputData, SystemStatus]:
        """Fetch and parse QPIGS/QPIGS2 ASCII data from Voltronic inverters."""
        # build and send ASCII requests
        req1 = create_ascii_request(self._next_transaction(), 0, "QPIGS")
        req2 = create_ascii_request(self._next_transaction(), 0, "QPIGS2")
        # send via send_bulk (which handles our TCP handshake)
        resps = await self.client.send_bulk([req1, req2])
        raw1 = decode_ascii_response(resps[0] if resps and resps[0] else "")
        raw2 = decode_ascii_response(resps[1] if len(resps) > 1 and resps[1] else "")

        p1 = raw1.split()
        p2 = raw2.split()

        vals: Dict[str, Any] = {}
        # Battery
        vals["battery_voltage"] = float(p1[8])
        ch = int(p1[9])
        dis = int(p1[15])
        vals["battery_current"] = ch - dis
        vals["battery_power"] = int(vals["battery_voltage"] * vals["battery_current"])
        vals["battery_soc"] = int(p1[10])
        vals["battery_temperature"] = int(p1[11])  # heat-sink temp

        # Output
        vals["output_voltage"] = float(p1[2])
        vals["output_current"] = float(p1[3])
        vals["output_power"] = int(p1[5])
        vals["output_apparent_power"] = int(p1[6])
        vals["output_load_percentage"] = int(p1[7])
        vals["output_frequency"] = float(p1[4])

        # PV1
        pv1p = int(p1[12])
        vals["pv_total_power"] = pv1p
        vals["pv_charging_power"] = pv1p
        vals["pv_charging_current"] = int(p1[13])
        vals["pv_temperature"] = int(p1[14])
        vals["pv1_voltage"] = float(p1[12])
        vals["pv1_current"] = float(p1[13])
        vals["pv1_power"] = pv1p

        # System & time
        mode = int(p1[17])
        try:
            opm = OperatingMode(mode)
        except ValueError:
            opm = OperatingMode.FAULT
        vals["system_mode"] = mode
        vals["inverter_time"] = _dt.utcnow().isoformat()
        # ASCII has no grid voltage → zero
        vals["grid_voltage"] = 0.0
        # grid_frequency = output_frequency
        vals["grid_frequency"] = vals["output_frequency"]

        # PV2 (if present)
        pv2p = 0.0
        if len(p2) >= 3:
            v2 = float(p2[1])
            i2 = float(p2[0])
            pv2p = int(p2[2])
            vals["pv2_voltage"] = v2
            vals["pv2_current"] = i2
            vals["pv2_power"] = pv2p
            vals["pv_total_power"] += pv2p
            vals["pv_charging_power"] += pv2p
            vals["pv_charging_current"] += i2
        else:
            vals["pv2_voltage"] = 0.0
            vals["pv2_current"] = 0.0
            vals["pv2_power"] = 0

        # final grid power after including PV2
        vals["grid_power"] = (
            vals["output_power"]
            + vals["battery_power"]
            - vals["pv_charging_power"]
        )

        # Build dataclasses
        battery = BatteryData(
            voltage=vals["battery_voltage"],
            current=vals["battery_current"],
            power=vals["battery_power"],
            soc=vals["battery_soc"],
            temperature=vals["battery_temperature"],
        )
        pv = PVData(
            total_power=vals["pv_total_power"],
            charging_power=vals["pv_charging_power"],
            charging_current=vals["pv_charging_current"],
            temperature=vals["pv_temperature"],
            pv1_voltage=vals["pv1_voltage"],
            pv1_current=int(vals["pv1_current"]),
            pv1_power=int(vals["pv1_power"]),
            pv2_voltage=vals["pv2_voltage"],
            pv2_current=int(vals["pv2_current"]),
            pv2_power=int(vals["pv2_power"]),
            pv_generated_today=None,
            pv_generated_total=None,
        )
        grid = GridData(
            voltage=vals["grid_voltage"],
            power=vals["grid_power"],
            frequency=int(vals["grid_frequency"]),
        )
        output = OutputData(
            voltage=vals["output_voltage"],
            current=vals["output_current"],
            power=vals["output_power"],
            apparent_power=vals["output_apparent_power"],
            load_percentage=vals["output_load_percentage"],
            frequency=int(vals["output_frequency"]),
        )
        system = SystemStatus(
            operating_mode=opm,
            mode_name=opm.name,
            inverter_time=vals["inverter_time"],
        )

        return battery, pv, grid, output, system
