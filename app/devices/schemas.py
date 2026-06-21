from datetime import datetime

from pydantic import BaseModel


class DeviceCreate(BaseModel):
    device_code: str
    device_name: str
    greenhouse_id: str
    protocol: str = "mqtt"


class DeviceUpdate(BaseModel):
    device_name: str | None = None
    greenhouse_id: str | None = None
    status: str | None = None


class DeviceOut(BaseModel):
    id: str
    device_code: str
    device_name: str
    greenhouse_id: str
    protocol: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SensorDataIn(BaseModel):
    device_code: str
    metric_type: str
    metric_value: float
    collected_at: datetime | None = None


class SensorDataOut(BaseModel):
    id: int
    device_id: str
    metric_type: str
    metric_value: float
    collected_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}
