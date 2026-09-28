import datetime as dt

import adafruit_tmp117
import board
from daq_tools.models import DataPoint

from ..core.base import BaseSensor, SensorErrorType, SensorResult
from ..core.config import SensorConfig
from ..core.logging import log


class TMP117Sensor(BaseSensor):
    SENSOR_TYPE = "tmp117"

    def __init__(self, config: SensorConfig, global_tags: dict[str, str]):
        super().__init__(config, global_tags)

        # tmp117-specific configuration from .extra
        self.i2c_address: int = int(config.extra.get("i2c_address", 0x48))
        self.tags["address"] = str(self.i2c_address)
        self._sensor: adafruit_tmp117.TMP117 | None = None

    async def initialize(self) -> None:
        """Initialize the tmp117 over I2C."""
        try:
            i2c = board.I2C()
            self._sensor = adafruit_tmp117.TMP117(i2c, address=self.i2c_address)
            log.success(f"TMP117 [{hex(self.i2c_address)}] initialized")
            if self._sensor.serial_number:
                self.tags["serial_number"] = str(self._sensor.serial_number)

        except Exception as e:
            log.error(f"Failed to initialize TMP117 at {hex(self.i2c_address)}: {e}")
            raise RuntimeError(
                f"Failed to initialize TMP117 at {hex(self.i2c_address)}: {e}"
            ) from e

    async def read(self) -> SensorResult:
        if not self._sensor:
            raise RuntimeError("tmp117 not initialized")

        datapoints: list[DataPoint] = []
        sample_time = dt.datetime.now(dt.UTC).timestamp()

        try:
            # Read all key values
            temperature = float(self._sensor.temperature)  # °C

            fields = {"temperature": round(temperature, 3)}

            # Add shunt resistance as metadata if desired
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields=fields,
            )
            datapoints.append(dp)
            return SensorResult(datapoints=datapoints)

        except Exception as e:
            log.warning(f"[tmp117:{self.name}] Read error: {e}")
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields={
                    "temperature": None,
                },
            )
            datapoints.append(dp)

            return SensorResult(
                datapoints=datapoints,
                success=False,
                error_type=SensorErrorType.COMMUNICATION,
                error_message=str(e),
            )

    async def shutdown(self) -> None:
        self._sensor = None
