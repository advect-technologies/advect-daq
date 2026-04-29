import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List
from .config import SensorConfig

from daq_tools.models import DataPoint


@dataclass
class SensorResult:
    """Container returned by sensor.read()"""
    datapoints: List[DataPoint]
    status: str = "ok"           # "ok", "error", "open_tc", etc.
    error: str | None = None


class BaseSensor(ABC):
    """Abstract base class for all Advect-DAQ sensors."""

    SENSOR_TYPE: str = "base"   # Must be overridden by each plugin

    def __init__(self, config: "SensorConfig", global_tags: Dict[str, str]):
        self.config: "SensorConfig" = config          # Keep reference to full config
        self.name: str = config.name
        self.interval: float = config.interval
        self.measurement: str = config.measurement or config.name

        # Merge global + per-sensor tags
        self.tags: Dict[str, str] = {**global_tags, **config.tags}

        if not self.name:
            raise ValueError(f"Sensor of type '{self.SENSOR_TYPE}' is missing a name")

    async def initialize(self) -> None:
        """Optional async initialization (open hardware, setup, etc.)."""
        pass

    @abstractmethod
    async def read(self) -> List[DataPoint]:
        """
        Return a list of DataPoints from this sensor.
        Multi-channel sensors (e.g. MCC134) should return one DataPoint per channel.
        Errors can be encoded as fields (e.g. error_code, status).
        """
        ...

    async def shutdown(self) -> None:
        """Optional cleanup when shutting down the engine."""
        pass