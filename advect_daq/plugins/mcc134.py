import datetime as dt
from typing import Dict, Any, List

from daqhats import mcc134, HatIDs, TcTypes, hat_list
from daq_tools.models import DataPoint

from ..core.base import BaseSensor
from ..core.config import SensorConfig


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

    async def initialize(self) -> None:
        """Initialize the MCC 134 board and configure thermocouple types."""
        hats = hat_list(filter_by_id=HatIDs.MCC_134)
        matching = [h for h in hats if h.address == self.address]
        
        if not matching:
            raise RuntimeError(f"MCC 134 board at address {self.address} not found")

        self.board = mcc134(self.address)

        # Configure TC types per channel
        for ch, tc_str in zip(self.channels, self.tc_types):
            tc_type = getattr(TcTypes, f"TYPE_{tc_str.upper()}", TcTypes.TYPE_K)
            self.board.tc_type_write(ch, tc_type)
            print(f"MCC134 [{self.address}] ch{ch} → Type {tc_str} (sensor: {self.name})")

    async def read(self) -> List[DataPoint]:
        if not self.board:
            raise RuntimeError("MCC134 board not initialized")

        datapoints: List[DataPoint] = []
        sample_time = dt.datetime.now(dt.timezone.utc).timestamp()

        for ch in self.channels:
            try:
                temp = self.board.t_in_read(ch)
                ch_tags = {**self.tags, "channel": str(ch)}

                if temp == mcc134.OPEN_TC_VALUE:
                    fields = {"temperature": None, "error_code": 1}
                elif temp == mcc134.OVERRANGE_TC_VALUE:
                    fields = {"temperature": None, "error_code": 2}
                else:
                    fields = {"temperature": round(float(temp), 2), "error_code": 0}

                dp = DataPoint(
                    time=sample_time,
                    measurement=self.measurement,
                    tags=ch_tags,
                    fields=fields
                )
                datapoints.append(dp)

            except Exception as e:
                dp = DataPoint(
                    time=sample_time,
                    measurement=self.measurement,
                    tags={**self.tags, "channel": str(ch)},
                    fields={"temperature": None, "error_code": 99}
                )
                datapoints.append(dp)
                print(f"[MCC134:{self.name}] Error reading channel {ch}: {e}")

        return datapoints