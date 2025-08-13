# models.py full code
from dataclasses import dataclass, field
from enum import Enum
import datetime
from typing import Dict, Optional, Callable, Any

@dataclass
class BatteryData:
    # live values
    voltage: Optional[float] = None
    current: Optional[float] = None           # net current (+ = charging, - = discharging)
    power: Optional[int] = None
    soc: Optional[int] = None
    temperature: Optional[int] = None
    # extras from ASCII QPIGS
    charge_current: Optional[float] = None    # Battery Charging Current (A)
    discharge_current: Optional[float] = None # Battery Discharge Current (A)
    voltage_scc: Optional[float] = None       # Battery Voltage from SCC (V)

@dataclass
class PVData:
    total_power: Optional[int] = None
    charging_power: Optional[int] = None
    charging_current: Optional[float] = None
    temperature: Optional[int] = None
    pv1_voltage: Optional[float] = None
    pv1_current: Optional[float] = None
    pv1_power: Optional[int] = None
    pv2_voltage: Optional[float] = None
    pv2_current: Optional[float] = None
    pv2_power: Optional[int] = None
    pv_generated_today: Optional[float] = None
    pv_generated_total: Optional[float] = None

@dataclass
class GridData:
    voltage: Optional[float] = None
    power: Optional[int] = None
    frequency: Optional[int] = None  # 5000 == 50.00Hz (kept for compatibility)

@dataclass
class OutputData:
    voltage: Optional[float] = None
    current: Optional[float] = None
    power: Optional[int] = None
    apparent_power: Optional[int] = None
    load_percentage: Optional[float] = None
    frequency: Optional[int] = None  # 5000 == 50.00Hz

class OperatingMode(Enum):
    FAULT = 0
    SUB = 2
    SBU = 3

@dataclass
class SystemStatus:
    operating_mode: OperatingMode
    mode_name: Optional[str] = None
    inverter_time: Optional[datetime.datetime] = None
    # extras (ASCII)
    output_mode_qpiri: Optional[str] = None
    device_status: Optional[str] = None        # raw QPIGS device status bits
    device_status2: Optional[str] = None       # raw QPIGS device status 2 bits
    warnings: Optional[str] = None             # parsed QPIWS warnings string

@dataclass
class RegisterConfig:
    """Configuration for a single register."""
    address: int
    scale_factor: float = 1.0
    processor: Optional[Callable[[int], Any]] = None

@dataclass
class ModelConfig:
    """Complete configuration for an inverter model."""
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

# Define model configurations (unchanged)
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
        "pv_energy_today": RegisterConfig(0),
        "pv_energy_total": RegisterConfig(0),
    }
)

VOLTRONIC_ASCII = ModelConfig(
    name="VOLTRONIC_ASCII",
    register_map={}
)

MODEL_CONFIGS = {
    "ISOLAR_SMG_II_11K": ISOLAR_SMG_II_11K,
    "ISOLAR_SMG_II_6K": ISOLAR_SMG_II_6K,
    "VOLTRONIC_ASCII": VOLTRONIC_ASCII,
}
