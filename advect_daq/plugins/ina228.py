import datetime as dt
from typing import Dict, List

import board
import adafruit_ina228
from daq_tools.models import DataPoint

from ..core.base import BaseSensor
from ..core.config import SensorConfig
from ..core.logging import log

class INA228Sensor(BaseSensor):
    """Adafruit INA228 High-Side/Low-Side Power Monitor plugin."""

    SENSOR_TYPE = "ina228"

    def __init__(self, config: SensorConfig, global_tags: Dict[str, str]):
        super().__init__(config, global_tags)

        # INA228-specific configuration from .extra
        self.i2c_address: int = int(config.extra.get("i2c_address", 0x40))
        self.shunt_resistance: float = float(config.extra.get("shunt_resistance", 0.015))  # ohms
        self.tags['address'] = self.i2c_address 
        self.ina = None

    async def initialize(self) -> None:
        """Initialize the INA228 over I2C."""
        try:
            i2c = board.I2C()  # Uses Blinka on Raspberry Pi
            self.ina = adafruit_ina228.INA228(i2c, address=self.i2c_address)

            # Optional: Configure shunt resistance (important for accurate current/power)
            # The library handles calibration internally when you set it this way in newer versions
            log.success(f"INA228 [{hex(self.i2c_address)}] initialized - Shunt: {self.shunt_resistance} Ω")
            log.success(f"INA228 sensor '{self.name}' ready")

        except Exception as e:
            raise RuntimeError(f"Failed to initialize INA228 at {hex(self.i2c_address)}: {e}") from e

    async def read(self) -> List[DataPoint]:
        if not self.ina:
            raise RuntimeError("INA228 not initialized")

        datapoints: List[DataPoint] = []
        sample_time = dt.datetime.now(dt.timezone.utc).timestamp()

        try:
            # Read all key values
            bus_voltage = float(self.ina.bus_voltage)          # V
            shunt_voltage = float(self.ina.shunt_voltage)      # V (often converted to mV)
            current = float(self.ina.current)                  # mA
            power = float(self.ina.power)                      # mW
            energy = float(self.ina.energy)                    # J (accumulated)
            die_temp = float(self.ina.die_temperature)         # °C

            fields = {
                "bus_voltage": round(bus_voltage, 4),
                "shunt_voltage": round(shunt_voltage * 1000, 3),   # mV
                "current": round(current, 3),                      # mA
                "power": round(power, 3),                          # mW
                "energy": round(energy, 3),                        # J
                "die_temperature": round(die_temp, 2),
                "error_code": 0
            }

            # Add shunt resistance as metadata if desired
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields=fields
            )
            datapoints.append(dp)

        except Exception as e:
            log.warning(f"[INA228:{self.name}] Read error: {e}")
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields={
                    "bus_voltage": None,
                    "current": None,
                    "power": None,
                    "error_code": 99
                }
            )
            datapoints.append(dp)

        return datapoints

    async def shutdown(self) -> None:
        self.ina = None