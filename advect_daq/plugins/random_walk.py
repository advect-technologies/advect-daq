import datetime as dt
import random
from typing import List, Dict

from daq_tools.models import DataPoint

from ..core.base import BaseSensor
from ..core.config import SensorConfig
from ..core.logging import log


class RandomSensor(BaseSensor):
    """Dummy sensor that generates random data - useful for testing."""

    SENSOR_TYPE = "random_walk"


    def __init__(self, config: SensorConfig, global_tags: Dict[str, str]):
        super().__init__(config, global_tags)
        self.max_values = self.config.extra.get('max_values',4)
        self.values = list(range(self.max_values))
        log.success('Initialized Random Walk Sensor')

    async def read(self) -> List[DataPoint]:
        sample_time = dt.datetime.now(dt.timezone.utc).timestamp()

        # Generate 1 to 4 random values
        num_values = random.randint(1, self.max_values)
 
        self.values = [round(v + random.gauss(sigma=0.1),3) for v in self.values]
 
        fields = {f"value_{i}": self.values[i] for i in range(num_values)}
        
        fields["error_code"] = 0

        dp = DataPoint(
            time=sample_time,
            measurement=self.measurement,
            tags=self.tags,
            fields=fields
        )

        # Occasionally simulate an error
        if random.random() < 0.05:   # 5% chance
            dp.fields = {"error_code": 99}
            log.warning(f"[RandomSensor:{self.name}] Simulated error")

        return [dp]