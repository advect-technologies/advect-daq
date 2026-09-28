"""Sensirion SDP8xx-Digital differential pressure plugin.

Continuous-measurement I2C driver for the SDP800 / SDP810 family
(±125 Pa and ±500 Pa, addresses 0x25 / 0x26). One instance per
(bus, address) pair — use separate Linux I2C buses for multiple
sensors on a Pi Zero 2W.

Requires ``smbus2``. I2C transfers run in a worker thread so polling
several buses does not block the engine event loop.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

from daq_tools.models import DataPoint
from smbus2 import SMBus, i2c_msg

from ..core.base import BaseSensor, SensorErrorType, SensorResult
from ..core.config import SensorConfig
from ..core.logging import log

# Continuous-mode start commands (datasheet §6.3.1)
_CMD_CONT_MASS_FLOW_AVG = 0x3603
_CMD_CONT_MASS_FLOW_RAW = 0x3608
_CMD_CONT_DP_AVG = 0x3615
_CMD_CONT_DP_RAW = 0x361E
_CMD_STOP = 0x3FF9
_CMD_PRODUCT_ID_1 = 0x367C
_CMD_PRODUCT_ID_2 = 0xE102
_CMD_SOFT_RESET_GENERAL = 0x0006  # I2C general call + 0x06

_TEMP_SCALE = 200.0
_DEFAULT_ADDRESS = 0x25
_DEFAULT_BUS = 1

# Product number with revision nibble masked off (datasheet §6.3.6)
_PRODUCT_INFO: dict[int, dict[str, Any]] = {
    0x03020100: {"model": "SDP800-500Pa", "range_pa": 500},
    0x03020A00: {"model": "SDP810-500Pa", "range_pa": 500},
    0x03020400: {"model": "SDP801-500Pa", "range_pa": 500},
    0x03020D00: {"model": "SDP811-500Pa", "range_pa": 500},
    0x03020200: {"model": "SDP800-125Pa", "range_pa": 125},
    0x03020B00: {"model": "SDP810-125Pa", "range_pa": 125},
}


def _crc8(data: bytes) -> int:
    """Sensirion CRC-8: poly 0x31, init 0xFF, CRC(0xBEEF) == 0x92."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _s16(msb: int, lsb: int) -> int:
    value = (msb << 8) | lsb
    if value & 0x8000:
        value -= 0x10000
    return value


def _u16(msb: int, lsb: int) -> int:
    return (msb << 8) | lsb


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    return int(text, 0)


def _crc_word(buf: bytes, offset: int) -> tuple[int, int]:
    """Return (msb, lsb) after verifying the CRC byte at offset+2."""
    word = buf[offset : offset + 2]
    crc = buf[offset + 2]
    if _crc8(word) != crc:
        raise ValueError(
            f"CRC mismatch at byte {offset}: data={word.hex()} crc={crc:#04x}"
        )
    return word[0], word[1]


class SDP8xxSensor(BaseSensor):
    """Sensirion SDP8xx-Digital continuous-mode differential pressure sensor."""

    SENSOR_TYPE = "sdp8xx"

    def __init__(self, config: SensorConfig, global_tags: dict[str, str]):
        super().__init__(config, global_tags)

        extra = config.extra
        self.i2c_bus: int = _parse_int(extra.get("i2c_bus"), _DEFAULT_BUS)
        self.i2c_address: int = _parse_int(extra.get("i2c_address"), _DEFAULT_ADDRESS)
        self.temp_comp: str = str(
            extra.get("temp_comp", "differential_pressure")
        ).lower()
        self.averaging: bool = bool(extra.get("averaging", True))
        self.soft_reset: bool = bool(extra.get("soft_reset", False))

        if self.temp_comp not in {"differential_pressure", "mass_flow"}:
            raise ValueError(
                f"[{self.name}] temp_comp must be 'differential_pressure' or "
                f"'mass_flow', got {self.temp_comp!r}"
            )
        if self.i2c_address not in {0x25, 0x26}:
            log.warning(
                f"[{self.name}] unusual SDP8xx address {self.i2c_address:#04x} "
                "(expected 0x25 or 0x26)"
            )

        self.tags["address"] = hex(self.i2c_address)
        self.tags["i2c_bus"] = str(self.i2c_bus)
        self.tags["temp_comp"] = self.temp_comp

        self._bus: SMBus | None = None
        self._start_cmd = self._select_start_command()

    def _select_start_command(self) -> int:
        if self.temp_comp == "mass_flow":
            return (
                _CMD_CONT_MASS_FLOW_AVG if self.averaging else _CMD_CONT_MASS_FLOW_RAW
            )
        return _CMD_CONT_DP_AVG if self.averaging else _CMD_CONT_DP_RAW

    # ------------------------------------------------------------------ I2C

    def _write_command(self, command: int, address: int | None = None) -> None:
        if self._bus is None:
            raise RuntimeError("I2C bus is not open")
        addr = self.i2c_address if address is None else address
        msg = i2c_msg.write(addr, [(command >> 8) & 0xFF, command & 0xFF])
        self._bus.i2c_rdwr(msg)

    def _read_bytes(self, length: int) -> bytes:
        if self._bus is None:
            raise RuntimeError("I2C bus is not open")
        msg = i2c_msg.read(self.i2c_address, length)
        self._bus.i2c_rdwr(msg)
        return bytes(msg)

    def _soft_reset_general_call(self) -> None:
        """I2C general-call reset. Resets every device on this bus."""
        if self._bus is None:
            raise RuntimeError("I2C bus is not open")
        msg = i2c_msg.write(0x00, [_CMD_SOFT_RESET_GENERAL & 0xFF])
        self._bus.i2c_rdwr(msg)
        time.sleep(0.002)

    def _read_identity(self) -> None:
        self._write_command(_CMD_PRODUCT_ID_1)
        self._write_command(_CMD_PRODUCT_ID_2)
        raw = self._read_bytes(18)

        p0, p1 = _crc_word(raw, 0)
        p2, p3 = _crc_word(raw, 3)
        product = (p0 << 24) | (p1 << 16) | (p2 << 8) | p3

        serial_bytes = bytearray()
        for offset in (6, 9, 12, 15):
            hi, lo = _crc_word(raw, offset)
            serial_bytes.extend((hi, lo))
        serial = int.from_bytes(serial_bytes, "big")

        info = _PRODUCT_INFO.get(product & 0xFFFFFF00, {})
        model = info.get("model", f"SDP8xx-{product:#010x}")
        range_pa = info.get("range_pa")

        self.tags["product_id"] = f"{product:#010x}"
        self.tags["serial"] = f"{serial:016x}"
        self.tags["model"] = model
        if range_pa is not None:
            self.tags["range_pa"] = str(range_pa)

        log.success(
            f"SDP8xx [{self.name}] {model} serial={serial:016x} "
            f"bus={self.i2c_bus} addr={self.i2c_address:#04x}"
        )

    def _start_continuous(self) -> None:
        self._write_command(self._start_cmd)
        time.sleep(0.012)

    def _stop_continuous(self) -> None:
        try:
            self._write_command(_CMD_STOP)
            time.sleep(0.001)
        except Exception as exc:
            log.warning(f"[{self.name}] stop continuous failed: {exc}")

    def _sync_initialize(self) -> None:
        self._bus = SMBus(self.i2c_bus)
        if self.soft_reset:
            log.warning(
                f"[{self.name}] issuing I2C general-call reset on bus {self.i2c_bus}"
            )
            self._soft_reset_general_call()
            time.sleep(0.025)

        try:
            self._read_identity()
        except Exception as exc:
            log.warning(f"[{self.name}] product id read failed (continuing): {exc}")

        self._start_continuous()
        log.success(
            f"SDP8xx [{self.name}] continuous mode started "
            f"(cmd={self._start_cmd:#06x}, averaging={self.averaging}, "
            f"temp_comp={self.temp_comp})"
        )

    def _sync_read_frame(self) -> tuple[float, float, int]:
        raw = self._read_bytes(9)
        if len(raw) != 9:
            raise RuntimeError(f"short I2C read ({len(raw)} bytes)")

        dp_m, dp_l = _crc_word(raw, 0)
        t_m, t_l = _crc_word(raw, 3)
        s_m, s_l = _crc_word(raw, 6)

        scale = _u16(s_m, s_l)
        if scale == 0:
            raise RuntimeError("sensor reported scale factor 0")

        dp_pa = _s16(dp_m, dp_l) / scale
        temp_c = _s16(t_m, t_l) / _TEMP_SCALE
        return dp_pa, temp_c, scale

    def _sync_shutdown(self) -> None:
        if self._bus is None:
            return
        try:
            self._stop_continuous()
        finally:
            try:
                self._bus.close()
            except Exception:
                log.error("Failed to close SDP8xx bus")
            self._bus = None

    # ---------------------------------------------------------------- async

    async def initialize(self) -> None:
        try:
            await asyncio.to_thread(self._sync_initialize)
        except Exception as exc:
            log.error(
                f"Failed to initialize SDP8xx [{self.name}] "
                f"bus={self.i2c_bus} addr={self.i2c_address:#04x}: {exc}"
            )
            raise RuntimeError(
                f"Failed to initialize SDP8xx [{self.name}] "
                f"bus={self.i2c_bus} addr={self.i2c_address:#04x}: {exc}"
            ) from exc

    async def read(self) -> SensorResult:
        if self._bus is None:
            raise RuntimeError("SDP8xx not initialized")

        sample_time = dt.datetime.now(dt.UTC).timestamp()
        try:
            dp_pa, temp_c, scale = await asyncio.to_thread(self._sync_read_frame)
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields={
                    "differential_pressure_pa": round(dp_pa, 4),
                    "temperature_c": round(temp_c, 3),
                    "scale_factor": scale,
                    "error_code": 0,
                },
            )
            return SensorResult(datapoints=[dp])
        except Exception as exc:
            log.warning(f"[SDP8xx:{self.name}] Read error: {exc}")
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields={
                    "differential_pressure_pa": None,
                    "temperature_c": None,
                    "scale_factor": None,
                    "error_code": 99,
                },
            )
            return SensorResult(
                datapoints=[dp],
                success=False,
                error_type=SensorErrorType.COMMUNICATION,
                error_message=str(exc),
            )

    async def shutdown(self) -> None:
        await asyncio.to_thread(self._sync_shutdown)
