import logging
import sys
from pathlib import Path

from loguru import logger


class InterceptHandler(logging.Handler):
    """Intercept standard logging and redirect to loguru."""

    def emit(self, record: logging.LogRecord):
        # Convert logging level to loguru level
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        logger.opt(depth=6, exception=record.exc_info).log(
            level, record.getMessage()
        )


def setup_logging(
    log_level: str = "INFO",
    log_to_file: bool = False,
    log_dir: str = "logs",
    retention_days: int = 7,
) -> None:
    """
    Configure beautiful loguru logging with full standard logging interception.
    """
    # Remove any existing handlers
    logger.remove()

    # Level mapping
    level_map = {
        "DEBUG": 10,
        "INFO": 20,
        "SUCCESS": 25,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }
    level_no = level_map.get(log_level.upper(), 20)

    # === Console Output (Colorful) ===
    logger.add(
        sys.stdout,
        level=level_no,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:8}</level> | <cyan>{name:12}</cyan> | {message}",
        enqueue=True,          # Thread-safe / asyncio friendly
    )

    # === Optional File Logging ===
    if log_to_file:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_path / "advect-daq_{time:YYYY-MM-DD}.log",
            level=level_no,
            rotation="00:00",           # Rotate at midnight
            retention=f"{retention_days} days",
            compression="zip",
            enqueue=True,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {name:12} | {message}",
        )
        logger.success(f"File logging enabled → {log_path}/")

    # === Intercept standard Python logging (important for daq_tools) ===
    logging.basicConfig(handlers=[InterceptHandler()], level=level_no, force=True)

    # Also patch root logger to ensure third-party logs are captured
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(InterceptHandler())
    root_logger.setLevel(level_no)

    logger.success(f"Advect-DAQ logging initialized at level {log_level}")


# Global logger instance to import everywhere
log = logger