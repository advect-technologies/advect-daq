import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import tomllib

logger = logging.getLogger(__name__)


@dataclass
class WriterConfig:
    output_dir: Path = field(default=Path("data"))
    batch_size: int = 100
    flush_interval: float = 10.0

@dataclass
class LoggingConfig:
    level: str = "INFO"
    to_file: bool = False
    log_dir: str = "logs"
    retention_days: int = 7

@dataclass
class SensorConfig:
    type: str
    name: str = ""
    interval: float = 1.0
    measurement: str = ""
    tags: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.measurement and self.name:
            self.measurement = self.name


@dataclass
class AdvectConfig:
    writer: WriterConfig
    sensors: List[SensorConfig]
    logging: LoggingConfig
    global_tags: Dict[str, str] = field(default_factory=dict)
    ingestor_config: str = "config/data_config.toml"

    @classmethod
    def from_toml(cls, path: str | Path = "config/sensors.toml") -> "AdvectConfig":
        path = Path(path)

        # === Handle sensors.toml fallback ===
        if not path.exists():
            logger.warning(f"⚠️  sensors.toml not found at {path}. Falling back to default_sensors.toml")
            path = Path("config/default_sensors.toml")
            if not path.exists():
                raise FileNotFoundError(
                    f"Neither sensors.toml nor default_sensors.toml found in config/ directory."
                )

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

        # === Handle ingestor_config fallback ===
        ingestor_config = global_data.get("ingestor_config", "config/data_config.toml")
        ingestor_path = Path(ingestor_config)

        if not ingestor_path.exists():
            logger.warning(f"⚠️  Ingestor config not found at {ingestor_path}. Falling back to default_data_config.toml")
            fallback_path = Path("config/default_data_config.toml")
            if fallback_path.exists():
                ingestor_config = str(fallback_path)
            else:
                logger.error(f"❌ Neither {ingestor_path} nor default_data_config.toml found!")
                # We'll still proceed but DAQIngestor will likely fail later

        # === Logging config ===
        logging_data = global_data.get("logging", {})
        logging_config = LoggingConfig(
            level=logging_data.get("level", "INFO"),
            to_file=logging_data.get("to_file", False),
            log_dir=logging_data.get("log_dir", "logs"),
            retention_days=logging_data.get("retention_days", 7),
        )

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
                enabled=s.get("enabled", True),
            )

            known_keys = {"type", "name", "interval", "measurement", "tags", "enabled"}
            sensor_config.extra = {k: v for k, v in s.items() if k not in known_keys}

            sensors.append(sensor_config)

        if not sensors:
            logger.warning("No sensors defined in configuration.")

        return cls(
            writer=writer_config,
            sensors=sensors,
            logging=logging_config,
            global_tags=global_tags,
            ingestor_config=ingestor_config
        )