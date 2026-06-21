from datetime import datetime

from pydantic import BaseModel


class HistoryQuery(BaseModel):
    greenhouse_id: str | None = None
    device_id: str | None = None
    metric_type: str | None = None
    start_time: datetime
    end_time: datetime
    aggregation: str | None = None


class AggregatedDataPoint(BaseModel):
    time_bucket: datetime
    avg_value: float | None
    min_value: float | None
    max_value: float | None
    count: int


class SensorDataPoint(BaseModel):
    id: int
    device_id: str
    metric_type: str
    metric_value: float
    collected_at: datetime

    model_config = {"from_attributes": True}
