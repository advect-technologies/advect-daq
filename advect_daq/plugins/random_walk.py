import datetime as dt
import random
from typing import List, Dict

from daq_tools.models import DataPoint

from ..core.base import BaseSensor, SensorResult, SensorErrorType
from ..core.config import SensorConfig
from ..core.logging import log


class RandomSensor(BaseSensor):
    """Dummy sensor that generates random data - useful for testing."""

    SENSOR_TYPE = "random_walk"

    def __init__(self, config: SensorConfig, global_tags: Dict[str, str]):
        super().__init__(config, global_tags)
        self.max_values = config.extra.get('max_values', 4)
        self.values = [round(random.uniform(20, 80), 3) for _ in range(self.max_values)]
        log.success(f'Initialized Random Walk Sensor: {self.name}')

    async def read(self) -> SensorResult:
        sample_time = dt.datetime.now(dt.timezone.utc).timestamp()

        # Random walk
        self.values = [round(v + random.gauss(0, 0.5), 3) for v in self.values]

        num_values = random.randint(1, self.max_values)
        fields = {f"value_{i}": self.values[i] for i in range(num_values)}
        fields["error_code"] = 0

        dp = DataPoint(
            time=sample_time,
            measurement=self.measurement,
            tags=self.tags,
            fields=fields
        )

        # Occasionally simulate an error
        if random.random() < 0.08:   # ~8% chance
            log.warning(f"[RandomWalk:{self.name}] Simulated error")
            return SensorResult(
                datapoints=[dp],
                success=False,
                error_type=SensorErrorType.DATA_QUALITY,
                error_message="Simulated sensor error"
            )

        return SensorResult(datapoints=[dp])