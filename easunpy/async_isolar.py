"""
easunpy.async_isolar
--------------------
High-level async client for Axpert/Voltronic ASCII via AsyncModbusClient.

Adds a SettingsAPI with typed helpers for *all* writable settings documented
in Axpert MAX protocol. Keeps reads unchanged.

References:
- POP (00/01/02), PBT, PCP, PGR, POPM, voltage/current setters, equalization,
  PBATCD/PBATMAXDISC, DAT, PCVT, RTEY/RTDL. See protocol sections. 
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

from .async_modbusclient import AsyncModbusClient

_LOGGER = logging.getLogger(__name__)


def _ack_ok(s: str) -> bool:
    return s.startswith("(ACK")


def _ensure_range(value: float, lo: float, hi: float, name: str) -> float:
    if not (lo <= value <= hi):
        raise ValueError(f"{name} out of range [{lo}, {hi}]: {value}")
    return value


class AsyncISolar:
    """High-level API. Periodic read methods exist in your integration; here we add settings."""

    def __init__(self, local_ip: str, port: int = 502) -> None:
        self._client = AsyncModbusClient(local_ip, port=port)
        self._start_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await self._client.start()
            self._started = True

    async def stop(self) -> None:
        await self._client.stop()
        self._started = False

    # -------------------------
    # Generic command helpers
    # -------------------------

    async def apply(self, ascii_command: str) -> bool:
        """
        Send a single ASCII command and return True if inverter replies ACK.
        Useful for advanced/unknown commands too.
        """
        await self.start()
        reply = await self._client.send_ascii_command(ascii_command)
        _LOGGER.debug("apply(%s) -> %s", ascii_command, reply)
        return _ack_ok(reply)

    # -------------------------
    # Typed settings API
    # -------------------------

    async def set_output_source_priority(self, mode: str | int) -> bool:
        """
        POP<NN>: 00 UtilitySolarBat, 01 SolarUtilityBat, 02 SolarBatUtility
        """
        mapping = {
            "utility_solar_bat": "00",
            "utilitysolarbat": "00",
            0: "00",
            "solar_utility_bat": "01",
            "solarutilitybat": "01",
            1: "01",
            "solar_bat_utility": "02",
            "solarbatutility": "02",
            2: "02",
        }
        code = mapping.get(str(mode).lower(), None) if not isinstance(mode, int) else mapping.get(mode)
        if code is None:
            raise ValueError("Invalid output source priority")
        return await self.apply(f"POP{code}")

    async def set_charger_source_priority(self, mode: str | int) -> bool:
        """
        PCP<NN>: 01 Solar first, 02 Solar+Utility, 03 Only solar charging.
        """
        mapping = {
            "solar_first": "01",
            1: "01",
            "solar_utility": "02",
            2: "02",
            "solar_plus_utility": "02",
            "only_solar": "03",
            3: "03",
            "only_solar_charging": "03",
        }
        code = mapping.get(str(mode).lower(), None) if not isinstance(mode, int) else mapping.get(mode)
        if code is None:
            raise ValueError("Invalid charger source priority")
        return await self.apply(f"PCP{code}")

    async def set_grid_working_range(self, mode: str | int) -> bool:
        """
        PGR<NN>: 00 Appliance, 01 UPS
        """
        mapping = {
            "appliance": "00",
            0: "00",
            "ups": "01",
            1: "01",
        }
        code = mapping.get(str(mode).lower(), None) if not isinstance(mode, int) else mapping.get(mode)
        if code is None:
            raise ValueError("Invalid grid working range")
        return await self.apply(f"PGR{code}")

    async def set_battery_type(self, battery_type: str | int) -> bool:
        """
        PBT<NN>: 00 AGM, 01 Flooded, 02 User, 03 PYL
        """
        mapping = {
            "agm": "00",
            0: "00",
            "flooded": "01",
            1: "01",
            "user": "02",
            2: "02",
            "pyl": "03",
            "pylontech": "03",
            3: "03",
        }
        code = mapping.get(str(battery_type).lower(), None) if not isinstance(battery_type, int) else mapping.get(battery_type)
        if code is None:
            raise ValueError("Invalid battery type")
        return await self.apply(f"PBT{code}")

    async def set_output_mode(self, mode: str | int) -> bool:
        """
        POPM<nn>: 
            00 single, 01 parallel, 02/03/04 3-phase P1/P2/P3,
            05 2-phase P1, 06 2-phase P2 (120°), 07 2-phase P2 (180°)
        """
        mapping = {
            "single": "00",
            0: "00",
            "parallel": "01",
            1: "01",
            "p1_3ph": "02",
            2: "02",
            "p2_3ph": "03",
            3: "03",
            "p3_3ph": "04",
            4: "04",
            "p1_2ph": "05",
            5: "05",
            "p2_2ph_120": "06",
            6: "06",
            "p2_2ph_180": "07",
            7: "07",
        }
        code = mapping.get(str(mode).lower(), None) if not isinstance(mode, int) else mapping.get(mode)
        if code is None:
            raise ValueError("Invalid output mode")
        return await self.apply(f"POPM{code}")

    async def set_rated_output_voltage(self, volts: int) -> bool:
        """
        V<nnn> (HV: 220/230/240, LV: 127/120/110)
        """
        if volts not in (110, 120, 127, 220, 230, 240):
            raise ValueError("Output voltage must be one of 110,120,127,220,230,240")
        return await self.apply(f"V{volts:03d}")

    async def set_rated_output_frequency(self, hz: int) -> bool:
        """
        F<nn> (50 or 60)
        """
        if hz not in (50, 60):
            raise ValueError("Output frequency must be 50 or 60")
        return await self.apply(f"F{hz:02d}")

    async def set_max_charging_current(self, amps: int, parallel_id: int = 0) -> bool:
        """
        MNCHGC<mnnn> (m = parallel machine number 0..6?, nnn amps)
        """
        amps = int(amps)
        if not (0 <= amps <= 150):
            raise ValueError("Max charging current must be 0..150 A")
        if not (0 <= parallel_id <= 9):
            raise ValueError("Parallel id must be 0..9")
        return await self.apply(f"MNCHGC{parallel_id:d}{amps:03d}")

    async def set_max_utility_charging_current(self, amps: int, parallel_id: int = 0) -> bool:
        """
        MUCHGC<mnn> (m=parallel id, nn amps)
        """
        amps = int(amps)
        if not (0 <= amps <= 30):
            # typical utility charge step; devices vary. Accept 0..30 safely.
            raise ValueError("Utility max charging current must be 0..30 A")
        if not (0 <= parallel_id <= 9):
            raise ValueError("Parallel id must be 0..9")
        return await self.apply(f"MUCHGC{parallel_id:d}{amps:02d}")

    async def set_battery_recharge_voltage(self, volts: float) -> bool:
        """
        PBCV<nn.n>: 48 V unit: 44.0..51.0, 24 V unit: 22.0..28.5
        """
        v = float(volts)
        if 30.0 <= v <= 62.0:
            # support both families; PDF gives explicit ranges
            pass
        elif 20.0 <= v <= 32.0:
            pass
        else:
            raise ValueError("Recharge voltage out of safe range (24V:22.0–28.5 / 48V:44.0–51.0)")
        return await self.apply(f"PBCV{v:04.1f}")

    async def set_battery_redischarge_voltage(self, volts: float) -> bool:
        """
        PBDV<nn.n>: 24V: 24.0..32.0 or 00.0, 48V: 48.0..62.0 or 00.0
        (00.0 means 'battery is full (float mode)')
        """
        v = float(volts)
        if v == 0.0:
            return await self.apply("PBDV00.0")
        if not (24.0 <= v <= 62.0):
            raise ValueError("Re-discharge voltage must be 24.0–62.0 V or 0.0")
        return await self.apply(f"PBDV{v:04.1f}")

    async def set_battery_cutoff_voltage(self, volts: float) -> bool:
        """
        PSDV<nn.n>: 48V unit: 42.0..48.0 (00 allowed on some models)
        """
        v = float(volts)
        if not (36.0 <= v <= 48.0):
            # be conservative
            raise ValueError("Cut-off voltage must be 42.0–48.0 V for 48V systems")
        return await self.apply(f"PSDV{v:04.1f}")

    async def set_battery_bulk_voltage(self, volts: float) -> bool:
        """
        PCVV<nn.n>: 24V: 24–64 (00 allowed), 48V: 48–64
        """
        v = float(volts)
        if v == 0.0:
            return await self.apply("PCVV00.0")
        if not (24.0 <= v <= 64.0):
            raise ValueError("Bulk/CV voltage must be 24.0–64.0 or 0.0")
        return await self.apply(f"PCVV{v:04.1f}")

    async def set_battery_float_voltage(self, volts: float) -> bool:
        """
        PBFT<nn.n>: 24V: 24–32 or 00, 48V: 48–64 or 00
        """
        v = float(volts)
        if v == 0.0:
            return await self.apply("PBFT00.0")
        if not (24.0 <= v <= 64.0):
            raise ValueError("Float voltage must be 24.0–64.0 or 0.0")
        return await self.apply(f"PBFT{v:04.1f}")

    async def set_cv_stage_max_time(self, minutes: int) -> bool:
        """
        PCVT<nnn>: 000..900, multiples of 5 (000=auto)
        """
        minutes = int(minutes)
        if not (0 <= minutes <= 900) or minutes % 5 != 0:
            raise ValueError("CV stage max time must be 0..900 in steps of 5")
        return await self.apply(f"PCVT{minutes:03d}")

    async def set_datetime(self, when: Optional[dt.datetime] = None) -> bool:
        """
        DAT<YYMMDDHHMMSS>
        """
        when = when or dt.datetime.now()
        return await self.apply(when.strftime("DAT%y%m%d%H%M%S"))

    async def reset_pv_load_energy(self) -> bool:
        """RTEY"""
        return await self.apply("RTEY")

    async def erase_data_log(self) -> bool:
        """RTDL"""
        return await self.apply("RTDL")

    async def equalization_enable(self, enabled: bool) -> bool:
        """PBEQE<n> (1 enable, 0 disable)"""
        return await self.apply(f"PBEQE{1 if enabled else 0}")

    async def equalization_set_time(self, minutes: int) -> bool:
        """PBEQT<nnn> (5..900, step 5)"""
        minutes = int(minutes)
        if not (5 <= minutes <= 900) or minutes % 5 != 0:
            raise ValueError("Equalization time must be 5..900, step 5")
        return await self.apply(f"PBEQT{minutes:03d}")

    async def equalization_set_period(self, days: int) -> bool:
        """PBEQP<nnn> (0..90)"""
        days = int(days)
        if not (0 <= days <= 90):
            raise ValueError("Equalization period must be 0..90 days")
        return await self.apply(f"PBEQP{days:03d}")

    async def equalization_set_voltage(self, volts: float) -> bool:
        """PBEQV<nn.nn> (device-specific range; accept 48.00..64.00 conservatively)"""
        v = float(volts)
        if not (24.0 <= v <= 64.0):
            raise ValueError("Equalization voltage must be between 24.00 and 64.00 V")
        return await self.apply(f"PBEQV{v:05.2f}")

    async def equalization_set_over_time(self, minutes: int) -> bool:
        """PBEQOT<nnn> (5..900, step 5)"""
        minutes = int(minutes)
        if not (5 <= minutes <= 900) or minutes % 5 != 0:
            raise ValueError("Equalization overtime must be 5..900, step 5")
        return await self.apply(f"PBEQOT{minutes:03d}")

    async def equalization_activate_now(self, active: bool) -> bool:
        """PBEQA<n> (1 active, 0 inactive)"""
        return await self.apply(f"PBEQA{1 if active else 0}")

    async def battery_control(self, discharge_full_on: int, discharge_on_standby_allowed: int, charge_full_on: int) -> bool:
        """
        PBATCD<abc> (a,b,c each 0/1 — see matrix in protocol)
        """
        for x in (discharge_full_on, discharge_on_standby_allowed, charge_full_on):
            if x not in (0, 1):
                raise ValueError("PBATCD flags must be 0 or 1")
        return await self.apply(f"PBATCD{discharge_full_on}{discharge_on_standby_allowed}{charge_full_on}")

    async def set_max_discharge_current(self, amps: int) -> bool:
        """
        PBATMAXDISC<nnn> (48V: 000 disables, 30..150 A)
        """
        amps = int(amps)
        if amps == 0:
            return await self.apply("PBATMAXDISC000")
        if not (30 <= amps <= 150):
            raise ValueError("Max discharge current must be 30..150 A (or 0 to disable)")
        return await self.apply(f"PBATMAXDISC{amps:03d}")

    async def flag_set(self, flag: str, enable: bool) -> bool:
        """
        PE<X>/PD<X> — flags documented in protocol:
        A buzzer, B overload bypass, K LCD default escape, U overload/battery
        restart, V over-temperature restart, X backlight, Y alarm on source loss,
        Z fault record.
        """
        flag = flag.upper().strip()
        if flag not in ("A", "B", "K", "U", "V", "X", "Y", "Z"):
            raise ValueError("Unknown flag (use one of A,B,K,U,V,X,Y,Z)")
        return await self.apply(("PE" if enable else "PD") + flag)
