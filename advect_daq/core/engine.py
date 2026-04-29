import asyncio
import logging
from typing import Dict

from .base import BaseSensor
from .config import AdvectConfig
from .writer import AsyncJsonlWriter
from ..utils.discovery import get_sensor_class

logger = logging.getLogger(__name__)


class AdvectEngine:
    """Main orchestrator for Advect-DAQ."""

    def __init__(self, config: AdvectConfig):
        self.config = config
        self.writer = AsyncJsonlWriter(config.writer)
        self.sensors: Dict[str, BaseSensor] = {}
        self.tasks: Dict[str, asyncio.Task] = {}

    async def initialize(self) -> None:
        """Initialize writer and all sensors."""
        await self.writer.start()

        for sensor_cfg in self.config.sensors:
            try:
                SensorClass = get_sensor_class(sensor_cfg.type)
                
                # Pass the full SensorConfig object + global_tags
                sensor = SensorClass(
                    config=sensor_cfg,                    # Now passing dataclass
                    global_tags=self.config.global_tags
                )
                
                await sensor.initialize()
                self.sensors[sensor.name] = sensor
                logger.info(f"Initialized sensor: {sensor.name} (type: {sensor_cfg.type})")
                
            except Exception as e:
                logger.error(f"Failed to initialize sensor '{sensor_cfg.name}': {e}", exc_info=True)

    async def _sensor_runner(self, sensor: BaseSensor):
        """Run periodic reads for a single sensor with backoff."""
        backoff = 1.0
        max_backoff = 60.0

        while True:
            try:
                datapoints = await sensor.read()

                for dp in datapoints:
                    await self.writer.write(dp)

                backoff = 1.0  # Reset backoff on success
                await asyncio.sleep(sensor.interval)

            except asyncio.CancelledError:
                logger.info(f"Shutting down sensor: {sensor.name}")
                await sensor.shutdown()
                raise
            except Exception as e:
                logger.error(f"Error in sensor {sensor.name}: {e}", exc_info=True)
                
                # Exponential backoff with cap
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def start(self) -> None:
        """Start all sensor runner tasks."""
        for name, sensor in self.sensors.items():
            task = asyncio.create_task(
                self._sensor_runner(sensor), 
                name=f"sensor_{name}"
            )
            self.tasks[name] = task

        logger.info(f"AdvectEngine started with {len(self.sensors)} sensor(s)")

    async def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down AdvectEngine...")

        # Cancel all sensor tasks
        for task in self.tasks.values():
            if not task.done():
                task.cancel()

        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)

        await self.writer.stop()
        logger.info("AdvectEngine shutdown complete")


async def run_advect_daq(config_path: str = "config/sensors.toml"):
    """Main entry point function."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    config = AdvectConfig.from_toml(config_path)
    
    engine = AdvectEngine(config)
    
    try:
        await engine.initialize()
        await engine.start()
        
        # Keep running until interrupted
        while True:
            await asyncio.sleep(3600)  # Sleep long, tasks run in background
            
    except asyncio.CancelledError:
        logger.info("Shutdown requested")
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Unexpected error in engine: {e}", exc_info=True)
    finally:
        await engine.stop()