# Creating New Sensor Plugins

This document explains how to add new sensors to **Advect-DAQ**.

## Quick Start

1. Create a new file in `advect_daq/plugins/` (e.g. `my_sensor.py`)
2. Implement a class that inherits from `BaseSensor`
3. Add it to your `sensors.toml` file
4. Restart the application

Plugins are **automatically discovered** — no registration needed.

---

## Plugin Template

```python
import datetime as dt
from typing import Dict, List

from daq_tools.models import DataPoint

from ..core.base import BaseSensor, SensorResult, SensorErrorType
from ..core.config import SensorConfig
from ..core.logging import log

class MyNewSensor(BaseSensor):
    """Short description of what this sensor does."""

    SENSOR_TYPE = "my_sensor"          # ← Must be unique and match TOML

    def __init__(self, config: SensorConfig, global_tags: Dict[str, str]):
        super().__init__(config, global_tags)
        
        # Access custom config options
        self.address = config.extra.get("address")
        self.some_setting = config.extra.get("some_setting", default_value)

    async def initialize(self) -> None:
        """Optional: Connect to hardware, configure, etc."""
        try:
            # Your initialization code here
            log.success(f"{self.name} initialized successfully")
        except Exception as e:
            log.error(f"Failed to initialize {self.name}: {e}")
            raise

    async def read(self) -> SensorResult:
        """Main method: Must return SensorResult"""
        sample_time = dt.datetime.now(dt.timezone.utc).timestamp()
        datapoints = []

        try:
            # Your sensor reading logic here
            value = ...  # read from hardware

            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields={
                    "value": round(value, 3),
                    "error_code": 0
                }
            )
            datapoints.append(dp)

            return SensorResult(datapoints=datapoints)

        except Exception as e:
            log.warning(f"[{self.name}] Read error: {e}")
            
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields={"error_code": 99}
            )
            datapoints.append(dp)

            return SensorResult(
                datapoints=datapoints,
                success=False,
                error_type=SensorErrorType.COMMUNICATION,
                error_message=str(e)
            )

    async def shutdown(self) -> None:
        """Optional cleanup."""
        pass
```
---
## Configuration Example (sensors.toml)

```toml
[[sensors]]
type = "my_sensor"           # Must match SENSOR_TYPE
name = "my_custom_sensor"
measurement = "my_custom_measurement"
interval = 5.0

# Any extra fields go here
address = 0x48
some_setting = "value"
```

## Error Handling Guidelines

Plugins are totally on their own for propagating any error info downstream to processed by the data handler. Usually the best way to accomplish this is through an `fault_code` field or something. However, as part of the `SensorResult` object returned by a plugins `read` method, the plugin should utilize the `base.SensorErrorType` to inform the engine about errors.  The engine will update logs and implement backoff/retry based upon severity of the issue.

| Error Type         | When to Use                                      | Dashboard Color | Severity |
|--------------------|--------------------------------------------------|-----------------|----------|
| `NONE`             | Normal successful reading                        | Green           | None     |
| `DATA_QUALITY`     | Got data but some values are bad (e.g. Open TC)  | Yellow          | Low      |
| `COMMUNICATION`    | Can see sensor but read failed                   | Red             | Medium   |
| `AVAILABILITY`     | Cannot reach sensor at all (board not found)     | Red             | High     |
| `UNKNOWN`          | Catch-all for unexpected errors                  | Red             | Medium   |

---

### Best Practices
- Keep `__init__` lightweight
- Put hardware connection/setup in `initialize()`
- Always return `SensorResult` (never raw list)
- Use `log.success()`, `log.info()`, `log.warning()`, `log.error()`
- Add useful tags (location, channel, serial number, etc.)
- Test with the `random_walk` sensor first




