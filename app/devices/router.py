import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_middleware import get_current_user
from app.database import get_db
from app.models import Device, SensorData, User
from app.devices.schemas import DeviceCreate, DeviceOut, DeviceUpdate

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
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    await db.delete(device)
