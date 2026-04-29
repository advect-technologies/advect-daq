import asyncio
from pathlib import Path
from typing import Optional

import aiofiles

from daq_tools.models import DataPoint
from .config import WriterConfig


class AsyncJsonlWriter:
    """Async batched JSONL writer with time-based flushing."""

    def __init__(self, config: WriterConfig):
        self.config = config
        self.queue: asyncio.Queue[DataPoint] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._buffer: list[DataPoint] = []
        self._next_flush_time: float = 0.0

    async def start(self) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self._next_flush_time = asyncio.get_running_loop().time() + self.config.flush_interval
        self._task = asyncio.create_task(self._writer_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._flush()

    async def write(self, dp: DataPoint) -> None:
        await self.queue.put(dp)

    async def _writer_loop(self):
        while True:
            try:
                remaining = max(0.0, self._next_flush_time - asyncio.get_running_loop().time())
                dp = await asyncio.wait_for(self.queue.get(), remaining)

                self._buffer.append(dp)

                if len(self._buffer) >= self.config.batch_size:
                    await self._flush()

            except asyncio.TimeoutError:
                await self._flush()
            except asyncio.CancelledError:
                await self._flush()
                raise
            except Exception as e:
                print(f"[Writer] Unexpected error: {e}")

    async def _flush(self):
        if not self._buffer:
            self._next_flush_time = asyncio.get_running_loop().time() + self.config.flush_interval
            return

        timestamp = int(asyncio.get_running_loop().time())
        filename = f"advect_{timestamp}.jsonl"
        file_path = self.config.output_dir / filename

        try:
            lines = [dp.to_json() for dp in self._buffer]
            async with aiofiles.open(file_path, "a", encoding="utf-8") as f:
                await f.write("\n".join(lines) + "\n")

            print(f"[Writer] Flushed {len(self._buffer)} DataPoints → {filename}")
            self._buffer.clear()

        except Exception as e:
            print(f"[Writer] Failed to write {file_path}: {e}")

        finally:
            self._next_flush_time = asyncio.get_running_loop().time() + self.config.flush_interval