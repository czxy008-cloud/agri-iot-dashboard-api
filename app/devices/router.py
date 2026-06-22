import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_middleware import get_current_user
from app.database import get_db
from app.models import AlertLog, Device, SensorData, User
from app.devices.schemas import DeviceCreate, DeviceOut, DeviceUpdate, SensorDataIn, SensorDataOut

router = APIRouter(prefix="/api/devices", tags=["设备管理"])


@router.post("/", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def create_device(
    body: DeviceCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.device_code == body.device_code))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="设备编码已存在")

    device = Device(
        id=uuid.uuid4(),
        device_code=body.device_code,
        device_name=body.device_name,
        greenhouse_id=body.greenhouse_id,
        protocol=body.protocol,
    )
    db.add(device)
    await db.flush()
    await db.refresh(device)
    return device


@router.get("/", response_model=list[DeviceOut])
async def list_devices(
    greenhouse_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    stmt = select(Device)
    if greenhouse_id:
        stmt = stmt.where(Device.greenhouse_id == greenhouse_id)
    result = await db.execute(stmt.order_by(Device.created_at.desc()))
    return result.scalars().all()


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    return device


@router.patch("/{device_id}", response_model=DeviceOut)
async def update_device(
    device_id: uuid.UUID,
    body: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(device, field, value)
    device.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(device)
    return device


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    force: bool = Query(default=False, description="是否强制删除（同时删除关联的传感器数据和告警日志）"),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"设备 ID={device_id} 不存在",
        )

    sensor_count = await db.scalar(
        select(func.count()).select_from(SensorData).where(SensorData.device_id == device_id)
    )
    alert_count = await db.scalar(
        select(func.count()).select_from(AlertLog).where(AlertLog.device_id == device_id)
    )

    has_related = (sensor_count or 0) + (alert_count or 0) > 0
    if has_related and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"该设备下仍有关联数据无法直接删除："
                f"传感器数据 {sensor_count or 0} 条，告警日志 {alert_count or 0} 条。"
                f"如需删除请添加 ?force=true 参数，或先清理关联数据。"
            ),
        )

    if force:
        await db.execute(
            text("DELETE FROM sensor_data WHERE device_id = :device_id"),
            {"device_id": device_id},
        )
        await db.execute(
            text("DELETE FROM alert_logs WHERE device_id = :device_id"),
            {"device_id": device_id},
        )

    await db.delete(device)


@router.post("/{device_id}/data", response_model=SensorDataOut, status_code=status.HTTP_201_CREATED)
async def submit_sensor_data(
    device_id: uuid.UUID,
    body: SensorDataIn,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """
    HTTP 传感器数据上报接口。
    当 MQTT 不可用时，可通过此接口直接提交传感器数据。
    同时会触发告警规则检查和 WebSocket 推送。
    """
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"设备 ID={device_id} 不存在",
        )

    collected_at = body.collected_at or datetime.now(timezone.utc)
    record = SensorData(
        device_id=device.id,
        metric_type=body.metric_type,
        metric_value=body.metric_value,
        collected_at=collected_at,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)

    try:
        from app.alerts.manager import check_alerts

        await check_alerts({
            "device_code": device.device_code,
            "metric_type": body.metric_type,
            "metric_value": body.metric_value,
            "collected_at": collected_at,
        })
    except Exception:
        pass

    return record
