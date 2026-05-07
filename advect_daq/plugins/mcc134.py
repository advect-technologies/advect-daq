import datetime as dt
from typing import Dict, Optional, List
from enum import IntEnum

from daqhats import mcc134, HatIDs, TcTypes, hat_list, HatError
from daq_tools.models import DataPoint

from ..core.base import BaseSensor, SensorResult, SensorErrorType
from ..core.config import SensorConfig
from ..core.logging import log

class ErrorCodes(IntEnum):
    NONE = 0
    OPEN_TC = 1
    OVERRANGE = 2
    COMMON_MODE = 3
    UNKNOWN = 99


class MCC134Sensor(BaseSensor):
    """MCC 134 Thermocouple HAT sensor plugin."""

    SENSOR_TYPE = "mcc134"

    def __init__(self, config: SensorConfig, global_tags: Dict[str, str]):
        super().__init__(config, global_tags)
        
        # Access sensor-specific config cleanly
        self.address: int = int(config.extra.get("address"))
        self.channels: List[int] = config.extra.get("channels", [0, 1, 2, 3])
        self.tc_types: List[str] = config.extra.get("tc_types", ["K"] * len(self.channels))
        self.board = None

        self.board = None
        self.serial_number: Optional[str] = None

    async def initialize(self) -> None:
        """Initialize the MCC 134 board and configure thermocouple types."""
        hats = hat_list(filter_by_id=HatIDs.MCC_134)
        matching = [h for h in hats if h.address == self.address]
        
        if not matching:
            raise RuntimeError(f"MCC 134 board at address {self.address} not found")

        self.board = mcc134(self.address)

        # Read serial number once during initialization
        try:
            self.serial_number = self.board.serial()
            log.success(f"MCC134 [{self.address}] initialized - SN: {self.serial_number}")
        except Exception as e:
            log.warning(f"MCC134 [{self.address}] Could not read serial number: {e}")
            # self.serial_number = f"addr_{self.address}"
            raise RuntimeError(f'MCC134 @ address:{self.address}, Unable to read serial number')

        # Configure TC types per channel
        for ch, tc_str in zip(self.channels, self.tc_types):
            tc_type = getattr(TcTypes, f"TYPE_{tc_str.upper()}", TcTypes.TYPE_K)
            self.board.tc_type_write(ch, tc_type)
            log.success(f"MCC134 [{self.address}] ch{ch} → Type {tc_str} (sensor: {self.name})")

    async def read(self) -> SensorResult:

        if not self.board:
            raise RuntimeError("MCC134 board not initialized")

        datapoints: List[DataPoint] = []
        errors = []
        error_levels: list[SensorErrorType] = []
        sample_time = dt.datetime.now(dt.timezone.utc).timestamp()

        for ch in self.channels:
            try:
                temp = self.board.t_in_read(ch)

                if temp == mcc134.OPEN_TC_VALUE:
                    fields = {"temperature": None, "error_code": ErrorCodes.OPEN_TC}
                    errors.append(f"Ch{ch}: Open TC")
                    error_levels.append(SensorErrorType.DATA_QUALITY)
                elif temp == mcc134.OVERRANGE_TC_VALUE:
                    fields = {"temperature": None, "error_code": ErrorCodes.OVERRANGE}
                    errors.append(f"Ch{ch}: Overrange")
                    error_levels.append(SensorErrorType.DATA_QUALITY)
                elif temp == mcc134.COMMON_MODE_TC_VALUE:
                    fields = {"temperature": None, "error_code": ErrorCodes.COMMON_MODE}
                    errors.append(f"Ch{ch}: Common mode")           
                    error_levels.append(SensorErrorType.DATA_QUALITY)         
                else:
                    fields = {"temperature": round(float(temp), 2), "error_code": ErrorCodes.NONE}

                dp = DataPoint(
                    time=sample_time,
                    measurement=self.measurement,
                    tags={**self.tags, "channel": str(ch),'sn':self.serial_number},
                    fields=fields
                )
                datapoints.append(dp)

            except HatError as e:
                errors.append(f"Ch{ch}: Hardware error")
                error_levels.append(SensorErrorType.COMMUNICATION)
                log.warning(f"[MCC134:{self.name}] HatError on channel {ch}: {e}")
                
                dp = DataPoint(
                    time=sample_time,
                    measurement=self.measurement,
                    tags={**self.tags, "channel": str(ch), "sn": self.serial_number},
                    fields={"temperature": None, "error_code": ErrorCodes.UNKNOWN}
                )
                datapoints.append(dp)

            except Exception as e:
                errors.append(f"Ch{ch}: {e}")
                error_levels.append(SensorErrorType.COMMUNICATION)
                log.warning(f"[MCC134:{self.name}] Error reading channel {ch}: {e}")

                dp = DataPoint(
                    time=sample_time,
                    measurement=self.measurement,
                    tags={**self.tags, "channel": str(ch), "sn": self.serial_number},
                    fields={"temperature": None, "error_code": ErrorCodes.UNKNOWN}
                )
                datapoints.append(dp)

        if errors:
            overall_error_type = max(error_levels) if error_levels else SensorErrorType.UNKNOWN
            return SensorResult(
                datapoints=datapoints,
                success=False,
                error_type=overall_error_type,
                error_message="; ".join(errors)
            )

        return SensorResult(datapoints=datapoints)