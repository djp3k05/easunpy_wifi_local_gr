from dataclasses import dataclass, field
from enum import Enum
import datetime
from typing import Dict, Optional, Callable, Any

# -------------------------
# Runtime data containers
# -------------------------

@dataclass
class BatteryData:
    voltage: Optional[float]
    current: Optional[float]
    power: Optional[int]
    soc: Optional[int]
    temperature: Optional[int]
    # Extras from QPIGS
    charge_current: Optional[float] = None
    discharge_current: Optional[float] = None
    voltage_scc: Optional[float] = None
    voltage_offset_for_fans: Optional[float] = None
    eeprom_version: Optional[str] = None

@dataclass
class PVData:
    total_power: Optional[int]
    charging_power: Optional[int]
    charging_current: Optional[float]
    temperature: Optional[int]
    pv1_voltage: Optional[float]
    pv1_current: Optional[float]
    pv1_power: Optional[int]
    pv2_voltage: Optional[float]
    pv2_current: Optional[float]
    pv2_power: Optional[int]
    pv_generated_today: Optional[float] = None
    pv_generated_total: Optional[float] = None

@dataclass
class GridData:
    voltage: Optional[float]
    power: Optional[int]
    frequency: Optional[int]

@dataclass
class OutputData:
    voltage: Optional[float]
    current: Optional[float]
    power: Optional[int]
    apparent_power: Optional[int]
    load_percentage: Optional[int]
    frequency: Optional[int]

# -------------------------
# Operating mode
# -------------------------

class OperatingMode(Enum):
    FAULT = 0
    SUB = 2
    SBU = 3
    IDLE = 4 # Added for standby mode from QMOD

# -------------------------
# “System / device” data
# -------------------------

@dataclass
class SystemStatus:
    operating_mode: OperatingMode
    mode_name: Optional[str] = None
    inverter_time: Optional[datetime.datetime] = None

    # Flags / warnings
    warnings: Optional[str] = None
    device_status_flags: Optional[str] = None
    device_status_flags2: Optional[str] = None
    bus_voltage: Optional[float] = None

    # QPIRI “ratings & settings”
    grid_rating_voltage: Optional[float] = None
    grid_rating_current: Optional[float] = None
    ac_output_rating_voltage: Optional[float] = None
    ac_output_rating_frequency: Optional[float] = None
    ac_output_rating_current: Optional[float] = None
    ac_output_rating_apparent_power: Optional[int] = None
    ac_output_rating_active_power: Optional[int] = None
    battery_rating_voltage: Optional[float] = None
    battery_recharge_voltage: Optional[float] = None
    battery_undervoltage: Optional[float] = None
    battery_bulk_voltage: Optional[float] = None
    battery_float_voltage: Optional[float] = None
    battery_type: Optional[str] = None
    max_ac_charging_current: Optional[float] = None
    max_charging_current: Optional[float] = None
    input_voltage_range: Optional[str] = None
    output_source_priority: Optional[str] = None
    charger_source_priority: Optional[str] = None
    parallel_max_num: Optional[int] = None
    machine_type: Optional[str] = None
    topology: Optional[str] = None
    output_mode_qpiri: Optional[str] = None
    battery_redischarge_voltage: Optional[float] = None
    pv_ok_condition: Optional[str] = None
    pv_power_balance: Optional[str] = None
    max_charging_time_cv: Optional[int] = None
    max_discharging_current: Optional[float] = None

# -------------------------
# Register mapping for Modbus models
# -------------------------

@dataclass
class RegisterConfig:
    address: int
    scale_factor: float = 1.0
    processor: Optional[Callable[[int], Any]] = None

@dataclass
class ModelConfig:
    name: str
    register_map: Dict[str, RegisterConfig] = field(default_factory=dict)
    def get_address(self, register_name: str) -> Optional[int]:
        config = self.register_map.get(register_name)
        return config.address if config else None
    def get_scale_factor(self, register_name: str) -> float:
        config = self.register_map.get(register_name)
        return config.scale_factor if config else 1.0
    def process_value(self, register_name: str, value: int) -> Any:
        config = self.register_map.get(register_name)
        if not config:
            return value
        if config.processor:
            return config.processor(value)
        return value * config.scale_factor

ISOLAR_SMG_II_11K = ModelConfig(
    name="ISOLAR_SMG_II_11K",
    register_map={
        "operation_mode": RegisterConfig(201),
        "battery_voltage": RegisterConfig(215, 0.1),
        "battery_current": RegisterConfig(216, 0.1),
        "battery_power": RegisterConfig(217),
        "battery_soc": RegisterConfig(229),
        "battery_temperature": RegisterConfig(226),
        "pv_total_power": RegisterConfig(223),
        "pv_charging_power": RegisterConfig(224),
        "pv_charging_current": RegisterConfig(234, 0.1),
        "pv_temperature": RegisterConfig(227),
        "pv1_voltage": RegisterConfig(219, 0.1),
        "pv1_current": RegisterConfig(220, 0.1),
        "pv1_power": RegisterConfig(221),
        "pv2_voltage": RegisterConfig(389, 0.1),
        "pv2_current": RegisterConfig(390, 0.1),
        "pv2_power": RegisterConfig(391),
        "grid_voltage": RegisterConfig(202, 0.1),
        "grid_power": RegisterConfig(204),
        "grid_frequency": RegisterConfig(203),
        "output_voltage": RegisterConfig(210, 0.1),
        "output_current": RegisterConfig(211, 0.1),
        "output_power": RegisterConfig(213),
        "output_apparent_power": RegisterConfig(214),
        "output_load_percentage": RegisterConfig(225, 0.01),
        "output_frequency": RegisterConfig(212),
        "time_register_0": RegisterConfig(696, processor=int),
        "time_register_1": RegisterConfig(697, processor=int),
        "time_register_2": RegisterConfig(698, processor=int),
        "time_register_3": RegisterConfig(699, processor=int),
        "time_register_4": RegisterConfig(700, processor=int),
        "time_register_5": RegisterConfig(701, processor=int),
        "pv_energy_today": RegisterConfig(702, 0.01),
        "pv_energy_total": RegisterConfig(703, 0.01),
    }
)

ISOLAR_SMG_II_6K = ModelConfig(
    name="ISOLAR_SMG_II_6K",
    register_map={
        "operation_mode": RegisterConfig(201),
        "battery_voltage": RegisterConfig(215, 0.1),
        "battery_current": RegisterConfig(216, 0.1),
        "battery_power": RegisterConfig(217),
        "battery_soc": RegisterConfig(229),
        "battery_temperature": RegisterConfig(226),
        "pv_total_power": RegisterConfig(223),
        "pv_charging_power": RegisterConfig(224),
        "pv_charging_current": RegisterConfig(234, 0.1),
        "pv_temperature": RegisterConfig(227),
        "pv1_voltage": RegisterConfig(219, 0.1),
        "pv1_current": RegisterConfig(220, 0.1),
        "pv1_power": RegisterConfig(223),
        "pv2_voltage": RegisterConfig(0),
        "pv2_current": RegisterConfig(0),
        "pv2_power": RegisterConfig(0),
        "grid_voltage": RegisterConfig(202, 0.1),
        "grid_current": RegisterConfig(0),
        "grid_power": RegisterConfig(204),
        "grid_frequency": RegisterConfig(203),
        "output_voltage": RegisterConfig(210, 0.1),
        "output_current": RegisterConfig(211, 0.1),
        "output_power": RegisterConfig(213),
        "output_apparent_power": RegisterConfig(214),
        "output_load_percentage": RegisterConfig(225, 0.01),
        "output_frequency": RegisterConfig(212),
        "time_register_0": RegisterConfig(696, processor=int),
        "time_register_1": RegisterConfig(697, processor=int),
        "time_register_2": RegisterConfig(698, processor=int),
        "time_register_3": RegisterConfig(699, processor=int),
        "time_register_4": RegisterConfig(700, processor=int),
        "time_register_5": RegisterConfig(701, processor=int),
    }
)

VOLTRONIC_ASCII = ModelConfig(name="VOLTRONIC_ASCII", register_map={})

MODEL_CONFIGS = {
    "ISOLAR_SMG_II_11K": ISOLAR_SMG_II_11K,
    "ISOLAR_SMG_II_6K": ISOLAR_SMG_II_6K,
    "VOLTRONIC_ASCII": VOLTRONIC_ASCII,
}