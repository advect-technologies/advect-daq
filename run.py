#!/usr/bin/env python3
"""
Advect-DAQ Full Runner with improved logging
"""

import asyncio
import signal
import sys
from pathlib import Path

# Windows asyncio fix — must be very early
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, str(Path(__file__).parent))

from advect_daq.core.config import AdvectConfig
from advect_daq.core.engine import AdvectEngine
from advect_daq.core.logging import setup_logging, log
from advect_daq.utils.discovery import discover_plugins, list_available_sensors

from daq_tools import DAQIngestor


async def main(config_path: str = "config/sensors.toml"):
    # Load config first
    config = AdvectConfig.from_toml(config_path)
    
    # === Setup Logging ===
    setup_logging(
        log_level=config.logging.level,
        log_to_file=config.logging.to_file,
        log_dir=config.logging.log_dir,
        retention_days=config.logging.retention_days,
    )

    log.info("=== Advect-DAQ Full System Starting ===")

    # Discover plugins
    discover_plugins()
    log.info(f"Available sensor types: {', '.join(list_available_sensors())}")

    engine = AdvectEngine(config)
    ingestor = None

    try:
        # Start sensor engine
        await engine.initialize()
        await engine.start()

        # Start DAQIngestor
        log.info(f"Starting DAQIngestor watching: {config.writer.output_dir}")
        log.info(f"Using ingestor config: {config.ingestor_config}")

        async with DAQIngestor.from_config_file(config.ingestor_config) as ingestor:
            log.success("✅ Advect-DAQ is now fully running (Sensors + Ingestor)")

            # Main keep-alive loop
            while True:
                await asyncio.sleep(30)

    except asyncio.CancelledError:
        log.info("Shutdown requested (CancelledError)")
    except KeyboardInterrupt:
        log.info("Shutdown requested (KeyboardInterrupt)")
    except Exception as e:
        log.exception("Unexpected error in main loop")
    finally:
        log.info("=== Starting graceful shutdown ===")
        
        await engine.stop()
        
        if ingestor is not None:
            log.info("Waiting for DAQIngestor to process final batch...")
            await asyncio.sleep(3.0)

        log.success("Advect-DAQ shutdown complete.")


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
        print("\nAdvect-DAQ stopped.")        
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        sys.exit(0)