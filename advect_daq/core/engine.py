import asyncio
from dataclasses import dataclass

from daq_tools.models import DataPoint

from ..utils.discovery import get_sensor_class
from .base import BaseSensor, SensorErrorType, SensorResult
from .config import AdvectConfig
from .logging import log
from .writer import AsyncJsonlWriter


@dataclass
class LiveEvent:
    """Fan-out payload for live-view subscribers. No backlog — drop if busy."""

    type: str
    sensor: str
    datapoints: list[DataPoint]
    success: bool
    error_type: SensorErrorType
    error_message: str | None
    healthy: bool


class AdvectEngine:
    """Main orchestrator for Advect-DAQ."""

    def __init__(self, config: AdvectConfig):
        self.config = config
        self.writer = AsyncJsonlWriter(config.writer)
        self.sensors: dict[str, BaseSensor] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.last_success: dict[str, float] = {}  # sensor_name -> timestamp
        self.latest_data: dict[str, list[DataPoint]] = {}
        self._last_write: dict[str, float] = {}
        self._live_subscribers: set[asyncio.Queue[LiveEvent]] = set()

    def subscribe_live(self) -> asyncio.Queue[LiveEvent]:
        queue: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=25)
        self._live_subscribers.add(queue)
        return queue

    def unsubscribe_live(self, queue: asyncio.Queue[LiveEvent]) -> None:
        self._live_subscribers.discard(queue)

    def _publish_live(self, event: LiveEvent) -> None:
        stale: list[asyncio.Queue[LiveEvent]] = []
        for queue in self._live_subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    stale.append(queue)
        for queue in stale:
            self.unsubscribe_live(queue)

    def _should_write(self, sensor: BaseSensor, now: float) -> bool:
        write_interval = sensor.config.write_interval
        if write_interval is None or write_interval <= sensor.interval:
            return True
        last = self._last_write.get(sensor.name)
        if last is None:
            return True
        return (now - last) >= write_interval

    async def initialize(self) -> None:
        """Initialize writer and all enabled sensors."""
        await self.writer.start()

        for sensor_cfg in self.config.sensors:
            if not sensor_cfg.enabled:
                log.info(f"Skipping disabled sensor: {sensor_cfg.name}")
                continue

            try:
                SensorClass = get_sensor_class(sensor_cfg.type)
                sensor = SensorClass(
                    config=sensor_cfg, global_tags=self.config.global_tags
                )

                await sensor.initialize()
                self.sensors[sensor.name] = sensor
                self.last_success[sensor.name] = asyncio.get_running_loop().time()

                log.info(f"Initialized sensor: {sensor.name} (type: {sensor_cfg.type})")

            except Exception as e:
                log.error(
                    f"Failed to initialize sensor '{sensor_cfg.name}': {e}",
                    exc_info=True,
                )

        if not self.sensors:
            log.warning("No sensors were successfully initialized")

    async def _sensor_runner(self, sensor: BaseSensor):
        """Run periodic reads for a single sensor with backoff."""
        backoff = 1.0
        max_backoff = 60.0

        while True:
            try:
                result: SensorResult = await sensor.read()
                now = asyncio.get_running_loop().time()
                usable = (
                    result.success or result.error_type <= SensorErrorType.DATA_QUALITY
                )

                if usable:
                    self.latest_data[sensor.name] = result.datapoints[:]
                    self.last_success[sensor.name] = now
                    if result.success:
                        sensor.record_success()
                    else:
                        sensor.record_error(
                            result.error_type, result.error_message or "Unknown error"
                        )
                    backoff = 1.0

                    if self._should_write(sensor, now):
                        for dp in result.datapoints:
                            await self.writer.write(dp)
                        self._last_write[sensor.name] = now
                else:
                    sensor.record_error(
                        result.error_type, result.error_message or "Unknown error"
                    )

                self._publish_live(
                    LiveEvent(
                        type="sample" if usable else "status",
                        sensor=sensor.name,
                        datapoints=result.datapoints[:],
                        success=result.success,
                        error_type=result.error_type,
                        error_message=result.error_message,
                        healthy=sensor.healthy,
                    )
                )

                await asyncio.sleep(sensor.interval)

            except asyncio.CancelledError:
                log.info(f"Shutting down sensor: {sensor.name}")
                await sensor.shutdown()
                raise
            except Exception as e:
                log.error(f"Error in sensor {sensor.name}: {e}", exc_info=True)
                self._publish_live(
                    LiveEvent(
                        type="status",
                        sensor=sensor.name,
                        datapoints=[],
                        success=False,
                        error_type=SensorErrorType.UNKNOWN,
                        error_message=str(e),
                        healthy=False,
                    )
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def start(self) -> None:
        """Start all sensor runner tasks and status summary."""
        for name, sensor in self.sensors.items():
            task = asyncio.create_task(
                self._sensor_runner(sensor), name=f"sensor_{name}"
            )
            self.tasks[name] = task

        log.info(f"AdvectEngine started with {len(self.sensors)} active sensor(s)")

    async def stop(self) -> None:
        """Graceful shutdown."""
        log.info("Shutting down AdvectEngine...")

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
