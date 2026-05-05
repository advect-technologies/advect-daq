import asyncio
from datetime import datetime
from typing import Dict

from .base import BaseSensor
from .config import AdvectConfig
from .writer import AsyncJsonlWriter
from ..utils.discovery import get_sensor_class

from .logging import log


class AdvectEngine:
    """Main orchestrator for Advect-DAQ."""

    def __init__(self, config: AdvectConfig):
        self.config = config
        self.writer = AsyncJsonlWriter(config.writer)
        self.sensors: Dict[str, BaseSensor] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.last_success: Dict[str, float] = {}      # sensor_name -> timestamp
        self.status_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Initialize writer and all enabled sensors."""
        await self.writer.start()

        for sensor_cfg in self.config.sensors:
            if not sensor_cfg.enabled:
                log.info(f"Skipping disabled sensor: {sensor_cfg.name}")
                continue

            try:
                SensorClass = get_sensor_class(sensor_cfg.type)
                sensor = SensorClass(config=sensor_cfg, global_tags=self.config.global_tags)
                
                await sensor.initialize()
                self.sensors[sensor.name] = sensor
                self.last_success[sensor.name] = asyncio.get_running_loop().time()
                
                log.info(f"Initialized sensor: {sensor.name} (type: {sensor_cfg.type})")
                
            except Exception as e:
                log.error(f"Failed to initialize sensor '{sensor_cfg.name}': {e}", exc_info=True)

        if not self.sensors:
            log.warning("No sensors were successfully initialized")

    async def _sensor_runner(self, sensor: BaseSensor):
        """Run periodic reads for a single sensor with backoff."""
        backoff = 1.0
        max_backoff = 60.0

        while True:
            try:
                datapoints = await sensor.read()

                for dp in datapoints:
                    await self.writer.write(dp)

                # Update last successful read time
                self.last_success[sensor.name] = asyncio.get_running_loop().time()
                backoff = 1.0

                await asyncio.sleep(sensor.interval)

            except asyncio.CancelledError:
                log.info(f"Shutting down sensor: {sensor.name}")
                await sensor.shutdown()
                raise
            except Exception as e:
                log.error(f"Error in sensor {sensor.name}: {e}", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _status_summary_task(self):
        """Periodic status summary every 5 minutes."""
        while True:
            await asyncio.sleep(300)  # 5 minutes
            self._print_status_summary()

    def _print_status_summary(self):
        """Print a clean status summary."""
        now = asyncio.get_running_loop().time()
        log.info("=== Advect-DAQ Status Summary ===")
        
        for name, sensor in self.sensors.items():
            last = self.last_success.get(name, 0)
            age = now - last if last > 0 else float('inf')
            
            if age < sensor.interval * 2:
                status = "OK"
            elif age < 300:
                status = "STALE"
            else:
                status = "ERROR"
                
            log.info(f"  {name:20} | Status: {status:6} | Last read: {age:6.1f}s ago | Interval: {sensor.interval:.1f}s")

        qsize = self.writer.queue.qsize() if hasattr(self.writer, 'queue') else 0
        log.info(f"  Active sensors: {len(self.sensors)} | Writer queue: {qsize}")
        log.info("===================================")

    async def start(self) -> None:
        """Start all sensor runner tasks and status summary."""
        for name, sensor in self.sensors.items():
            task = asyncio.create_task(self._sensor_runner(sensor), name=f"sensor_{name}")
            self.tasks[name] = task

        # Start periodic status summary
        self.status_task = asyncio.create_task(self._status_summary_task())

        log.info(f"AdvectEngine started with {len(self.sensors)} active sensor(s)")

    async def stop(self) -> None:
        """Graceful shutdown."""
        log.info("Shutting down AdvectEngine...")

        # Cancel status task
        if self.status_task and not self.status_task.done():
            self.status_task.cancel()
            try:
                await self.status_task
            except asyncio.CancelledError:
                pass

        # Cancel sensor tasks
        for task in self.tasks.values():
            if not task.done():
                task.cancel()

        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)

        await self.writer.stop()
        log.info("AdvectEngine shutdown complete")


# ====================== Helper Entry Point ======================
async def run_advect_daq(config_path: str = "config/sensors.toml"):
    """Main entry point function used by run.py"""
    config = AdvectConfig.from_toml(config_path)
    engine = AdvectEngine(config)
    
    try:
        await engine.initialize()
        await engine.start()
        
        # Keep the program running
        while True:
            await asyncio.sleep(3600)
            
    except asyncio.CancelledError:
        log.info("Shutdown requested")
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
    except Exception as e:
        log.error(f"Unexpected error in engine: {e}", exc_info=True)
    finally:
        await engine.stop()