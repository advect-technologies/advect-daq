from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import tomllib


@dataclass
class WriterConfig:
    output_dir: Path = field(default=Path("data"))
    batch_size: int = 100
    flush_interval: float = 10.0


@dataclass
class SensorConfig:
    type: str
    name: str = ""
    interval: float = 1.0
    measurement: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True                     # ← New
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.measurement and self.name:
            self.measurement = self.name

@dataclass
class AdvectConfig:
    writer: WriterConfig
    sensors: List[SensorConfig]
    global_tags: Dict[str, str] = field(default_factory=dict)
    ingestor_config: str = "data_config.toml"

    @classmethod
    def from_toml(cls, path: str | Path = "config/sensors.toml") -> "AdvectConfig":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "rb") as f:
            data = tomllib.load(f)

        global_data = data.get("global", {})
        writer_data = global_data.get("writer", {})

        writer_config = WriterConfig(
            output_dir=Path(writer_data.get("output_dir", "data")),
            batch_size=writer_data.get("batch_size", 100),
            flush_interval=writer_data.get("flush_interval", 10.0),
        )

        global_tags = global_data.get("tags", {})
        ingestor_config = global_data.get("ingestor_config", "data_config.toml")

        sensor_data = data.get("sensors", [])
        sensors: List[SensorConfig] = []
        name_counter: Dict[str, int] = {}

        for i, s in enumerate(sensor_data, 1):
            sensor_type = s.get("type")
            if not sensor_type:
                raise ValueError(f"Sensor #{i} missing required field 'type'")

            base_name = s.get("name") or f"{sensor_type}_{name_counter.get(sensor_type, 1)}"
            name_counter[sensor_type] = name_counter.get(sensor_type, 0) + 1

            sensor_config = SensorConfig(
                type=sensor_type,
                name=base_name,
                interval=float(s.get("interval", 1.0)),
                measurement=s.get("measurement", ""),
                tags=s.get("tags", {}),
                enabled=s.get("enabled", True),          # ← New
            )

            known_keys = {"type", "name", "interval", "measurement", "tags", "enabled"}
            sensor_config.extra = {k: v for k, v in s.items() if k not in known_keys}

            sensors.append(sensor_config)

        return cls(
            writer=writer_config,
            sensors=sensors,
            global_tags=global_tags,
            ingestor_config=ingestor_config
        )