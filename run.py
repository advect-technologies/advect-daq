#!/usr/bin/env python3
"""
Advect-DAQ Full Runner
Runs both AdvectEngine (sensors) and DAQIngestor concurrently with proper shutdown ordering.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from advect_daq.core.config import AdvectConfig
from advect_daq.core.engine import AdvectEngine
from advect_daq.utils.discovery import discover_plugins, list_available_sensors

from daq_tools import DAQIngestor


async def main(config_path: str = "config/sensors.toml"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logger = logging.getLogger("advect_daq")
    logger.info("=== Advect-DAQ Full System Starting ===")

    # Discover plugins
    discover_plugins()
    logger.info(f"Available sensor types: {', '.join(list_available_sensors())}")

    config = AdvectConfig.from_toml(config_path)
    engine = AdvectEngine(config)

    ingestor = None

    try:
        await engine.initialize()
        await engine.start()

        watch_dir = config.writer.output_dir
        logger.info(f"Starting DAQIngestor watching: {watch_dir}")
        logger.info(f"Using ingestor config: {config.ingestor_config}")

        async with DAQIngestor.from_config_file(config.ingestor_config) as ingestor:
            logger.info("✅ Advect-DAQ is now fully running (Sensors + Ingestor)")

            # Main keep-alive loop
            while True:
                await asyncio.sleep(30)

    except asyncio.CancelledError:
        logger.info("Shutdown requested (Cancelled)")
    except KeyboardInterrupt:
        logger.info("Shutdown requested (KeyboardInterrupt)")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
    finally:
        logger.info("=== Starting graceful shutdown ===")

        # Step 1: Stop sensor acquisition
        await engine.stop()

        # Step 2: Give DAQIngestor time to process the final files
        if ingestor is not None:
            logger.info("Waiting for DAQIngestor to process final batch...")
            await asyncio.sleep(3.0)   # Give ingestor time to pick up last files

        logger.info("Advect-DAQ shutdown complete.")


def setup_signal_handlers():
    def shutdown_handler(signum, frame):
        # This will be caught by the main try/except
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)


if __name__ == "__main__":
    setup_signal_handlers()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAdvect-DAQ stopped gracefully.")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)