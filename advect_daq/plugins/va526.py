"""CS Instruments VA 526 thermal mass-flow sensor (Modbus RTU).

Async Modbus RTU client for the VA 526. Compatible with pymodbus >= 3.10.
One instance per serial port + device ID. The engine polls ``read()`` at
the configured interval; this plugin performs a single register snapshot.

Register addresses and factory defaults taken from the VA 526 manual
(Table 24 Measurement register | Basic and Table 10 factory settings)
and the previously validated standalone reader.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Final

from daq_tools.models import DataPoint
from pymodbus.client import AsyncModbusSerialClient
from pymodbus.client.mixin import ModbusClientMixin
from pymodbus.exceptions import ModbusException

from ..core.base import BaseSensor, SensorErrorType, SensorResult
from ..core.config import SensorConfig
from ..core.logging import log

# Measurement registers (0-based holding-register addresses).
# Each float occupies 2 consecutive 16-bit registers.
REGISTERS: Final[dict[str, int]] = {
    "flow_m3_per_min": 1116,
    "flow_cfm": 1180,
    "flow_ncfm": 1188,
    "flow_kg_per_min": 1204,
    "flow_kg_per_s": 1212,
    "velocity_m_per_s": 1346,
    "velocity_nm_per_s": 1354,
    "gas_temp_c": 1418,
}

# Factory defaults (manual Table 10 / working reader)
_DEFAULT_BAUDRATE: Final[int] = 19200
_DEFAULT_PARITY: Final[str] = "E"
_DEFAULT_STOPBITS: Final[int] = 1
_DEFAULT_BYTESIZE: Final[int] = 8
_DEFAULT_DEVICE_ID: Final[int] = 1
_DEFAULT_TIMEOUT: Final[float] = 3.0
_WORD_ORDER: Final[str] = "big"  # ABCD / Big Endian


def _parse_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    return int(text, 0)


def _decode_float(registers: list[int]) -> float:
    """Decode two 16-bit registers into a 32-bit float (Big Endian / ABCD)."""
    value = ModbusClientMixin.convert_from_registers(
        registers,
        data_type=ModbusClientMixin.DATATYPE.FLOAT32,
        word_order=_WORD_ORDER,
    )
    if isinstance(value, list):
        return float(value[0])
    return float(value)


class VA526Sensor(BaseSensor):
    """CS Instruments VA 526 thermal mass-flow sensor via Modbus RTU."""

    SENSOR_TYPE = "va526"

    def __init__(self, config: SensorConfig, global_tags: dict[str, str]):
        super().__init__(config, global_tags)

        extra = config.extra
        self.port: str = str(extra.get("port") or extra.get("serial_port") or "")
        if not self.port:
            raise ValueError(f"[{self.name}] missing required 'port' (serial port)")

        self.baudrate: int = _parse_int(extra.get("baudrate"), _DEFAULT_BAUDRATE)
        self.parity: str = str(extra.get("parity", _DEFAULT_PARITY)).upper()
        self.stopbits: int = _parse_int(extra.get("stopbits"), _DEFAULT_STOPBITS)
        self.bytesize: int = _parse_int(extra.get("bytesize"), _DEFAULT_BYTESIZE)
        self.device_id: int = _parse_int(
            extra.get("device_id") or extra.get("slave_id"), _DEFAULT_DEVICE_ID
        )
        self.timeout: float = float(extra.get("timeout", _DEFAULT_TIMEOUT))

        if self.parity not in {"N", "E", "O"}:
            raise ValueError(f"[{self.name}] parity must be N/E/O, got {self.parity!r}")
        if self.stopbits not in {1, 2}:
            raise ValueError(
                f"[{self.name}] stopbits must be 1 or 2, got {self.stopbits}"
            )
        if self.bytesize not in {7, 8}:
            raise ValueError(
                f"[{self.name}] bytesize must be 7 or 8, got {self.bytesize}"
            )
        if not 1 <= self.device_id <= 247:
            raise ValueError(
                f"[{self.name}] device_id must be 1-247, got {self.device_id}"
            )

        self.tags["port"] = self.port
        self.tags["device_id"] = str(self.device_id)

        self._client: AsyncModbusSerialClient | None = None

    async def initialize(self) -> None:
        self._client = AsyncModbusSerialClient(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=self.timeout,
            retries=3,
        )
        try:
            connected = await self._client.connect()
            if not connected:
                raise RuntimeError("connect() returned False")
            log.success(
                f"VA526 [{self.name}] connected "
                f"port={self.port} baud={self.baudrate} "
                f"parity={self.parity} id={self.device_id}"
            )
        except Exception as exc:
            log.error(
                f"Failed to initialize VA526 [{self.name}] "
                f"port={self.port} id={self.device_id}: {exc}"
            )
            raise RuntimeError(
                f"Failed to initialize VA526 [{self.name}] "
                f"port={self.port} id={self.device_id}: {exc}"
            ) from exc

    async def _ensure_connected(self) -> None:
        if self._client is None:
            raise RuntimeError("VA526 client not created")
        if not self._client.connected:
            log.warning(f"[{self.name}] not connected, attempting reconnect")
            connected = await self._client.connect()
            if not connected:
                raise RuntimeError("reconnect failed")

    async def _read_one(self, name: str, address: int) -> float | None:
        assert self._client is not None
        try:
            response = await self._client.read_holding_registers(
                address=address,
                count=2,
                device_id=self.device_id,
            )
            if response.isError():
                log.warning(
                    f"[{self.name}] error reading {name} (addr {address}): {response}"
                )
                return None
            return _decode_float(response.registers)
        except ModbusException as exc:
            log.error(
                f"[{self.name}] Modbus exception reading {name} (addr {address}): {exc}"
            )
            return None
        except Exception as exc:
            log.exception(f"[{self.name}] unexpected error reading {name}: {exc}")
            return None

    async def read(self) -> SensorResult:
        sample_time = dt.datetime.now(dt.UTC).timestamp()
        if self._client is None:
            raise RuntimeError("VA526 not initialized")

        try:
            await self._ensure_connected()

            fields: dict[str, float | None] = {}
            any_success = False
            for name, address in REGISTERS.items():
                value = await self._read_one(name, address)
                fields[name] = None if value is None else round(value, 4)
                if value is not None:
                    any_success = True

            fields["error_code"] = 0 if any_success else 99

            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields=fields,
            )

            if any_success:
                return SensorResult(datapoints=[dp])

            return SensorResult(
                datapoints=[dp],
                success=False,
                error_type=SensorErrorType.COMMUNICATION,
                error_message="all register reads failed",
            )

        except Exception as exc:
            log.warning(f"[VA526:{self.name}] Read error: {exc}")
            fields = {name: None for name in REGISTERS}
            fields["error_code"] = 99
            dp = DataPoint(
                time=sample_time,
                measurement=self.measurement,
                tags=self.tags,
                fields=fields,
            )
            return SensorResult(
                datapoints=[dp],
                success=False,
                error_type=SensorErrorType.COMMUNICATION,
                error_message=str(exc),
            )

    async def shutdown(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
            log.info(f"VA526 [{self.name}] client closed")
        except Exception as exc:
            log.warning(f"[{self.name}] error closing client: {exc}")
        finally:
            self._client = None
