#!/usr/bin/env python3
"""
Advect-DAQ Full Runner
Runs both:
  - AdvectEngine (sensor acquisition + JSONL writing)
  - DAQIngestor (from daq_tools)
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

    # Discover sensor plugins
    discover_plugins()
    logger.info(f"Available sensor types: {', '.join(list_available_sensors())}")

    # Load configuration
    config = AdvectConfig.from_toml(config_path)
    engine = AdvectEngine(config)

    ingestor = None

    try:
        # === Start Advect Engine ===
        await engine.initialize()
        await engine.start()

        # === Start DAQIngestor ===
        watch_dir = config.writer.output_dir
        ingestor_config_path = config.ingestor_config

        logger.info(f"Starting DAQIngestor watching directory: {watch_dir}")
        logger.info(f"Using ingestor config: {ingestor_config_path}")

        async with DAQIngestor.from_config_file(ingestor_config_path) as ingestor:
            logger.info("✅ Both AdvectEngine and DAQIngestor are now running")

            # Main keep-alive loop
            while True:
                await asyncio.sleep(30)   # Check periodically

    except asyncio.CancelledError:
        logger.info("Shutdown requested via CancelledError")
    except KeyboardInterrupt:
        logger.info("Shutdown requested via KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
    finally:
        logger.info("=== Shutting down Advect-DAQ ===")
        
        await engine.stop()
        
        if ingestor is not None:
            logger.info("DAQIngestor context closed.")

        logger.info("Advect-DAQ shutdown complete.")


def setup_signal_handlers():
    def shutdown_handler(signum, frame):
        raise KeyboardInterrupt(f"Received signal {signum}")

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAdvect-DAQ stopped by user.")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)