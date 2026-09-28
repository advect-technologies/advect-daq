from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from daq_tools.models import DataPoint

from .config import SensorConfig


class SensorErrorType(IntEnum):
    NONE = 0
    AVAILABILITY = 3  # Sensor completely unreachable (e.g. board not found)
    COMMUNICATION = 2  # Can talk to sensor but read failed
    DATA_QUALITY = 1  # Got data but it's bad (open TC, overrange, etc.)
    UNKNOWN = 4


@dataclass
class SensorResult:
    """Standardized return value from sensor.read()"""

    datapoints: list[DataPoint]
    success: bool = True
    error_type: SensorErrorType = SensorErrorType.NONE
    error_message: str | None = None
    metadata: dict[str, Any] | None = None


class BaseSensor(ABC):
    """Abstract base class for all Advect-DAQ sensors."""

    SENSOR_TYPE: str = "base"  # Must be overridden by each plugin

    def __init__(self, config: "SensorConfig", global_tags: dict[str, str]):
        self.config: SensorConfig = config  # Keep reference to full config
        self.name: str = config.name
        self.interval: float = config.interval
        self.measurement: str = config.measurement or config.name
        self.enabled: bool = config.enabled

        # Merge global tags + per-sensor tags + auto-add sensor name
        self.tags: dict[str, str] = {
            **global_tags,
            **config.tags,
            "sensor": config.name,
        }

        # Health tracking
        self.last_error: str | None = None
        self.consecutive_errors: int = 0
        self.healthy: bool = True
        self.last_error_type: SensorErrorType = SensorErrorType.NONE

        if not self.name:
            raise ValueError(f"Sensor of type '{self.SENSOR_TYPE}' is missing a name")

    async def initialize(self) -> None:
        """Optional async initialization (open hardware, setup, etc.)."""

    @abstractmethod
    async def read(self) -> SensorResult:
        """
        Return a SensorResult from this sensor.
        Multi-channel sensors (e.g. MCC134) should return one DataPoint per channel.
        Errors that should propagate to db must be encoded as fields (e.g. error_code, status).
        """
        ...

    async def shutdown(self) -> None:
        """Optional cleanup when shutting down the engine."""

    def record_success(self):
        self.last_error = None
        self.consecutive_errors = 0
        self.healthy = True
        self.last_error_type = SensorErrorType.NONE

    def record_error(self, error_type: SensorErrorType, message: str):
        self.last_error = message
        self.consecutive_errors += 1
        self.healthy = False
        self.last_error_type = error_type
