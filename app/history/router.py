import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_middleware import get_current_user
from app.database import get_db
from app.models import Device, SensorData, User

router = APIRouter(prefix="/api/history", tags=["历史数据"])


@router.get("/query")
async def query_sensor_data(
    start_time: datetime = Query(..., description="查询起始时间"),
    end_time: datetime = Query(..., description="查询结束时间"),
    greenhouse_id: str | None = Query(None, description="大棚编号"),
    metric_type: str | None = Query(None, description="指标类型"),
    device_id: str | None = Query(None, description="设备ID"),
    limit: int = Query(default=1000, le=10000),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    stmt = select(SensorData).join(Device, SensorData.device_id == Device.id)

    stmt = stmt.where(
        SensorData.collected_at >= start_time,
        SensorData.collected_at <= end_time,
    )
    if greenhouse_id:
        stmt = stmt.where(Device.greenhouse_id == greenhouse_id)
    if metric_type:
        stmt = stmt.where(SensorData.metric_type == metric_type)
    if device_id:
        stmt = stmt.where(SensorData.device_id == device_id)

    stmt = stmt.order_by(SensorData.collected_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "device_id": str(r.device_id),
            "metric_type": r.metric_type,
            "metric_value": r.metric_value,
            "collected_at": r.collected_at.isoformat() if r.collected_at else None,
        }
        for r in rows
    ]


@router.get("/aggregate")
async def aggregate_sensor_data(
    start_time: datetime = Query(..., description="查询起始时间"),
    end_time: datetime = Query(..., description="查询结束时间"),
    greenhouse_id: str | None = Query(None, description="大棚编号"),
    metric_type: str | None = Query(None, description="指标类型"),
    interval_minutes: int = Query(default=60, description="聚合时间间隔(分钟)"),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    interval_expr = func.date_trunc(
        "hour",
        SensorData.collected_at,
    )

    stmt = select(
        interval_expr.label("time_bucket"),
        func.avg(SensorData.metric_value).label("avg_value"),
        func.min(SensorData.metric_value).label("min_value"),
        func.max(SensorData.metric_value).label("max_value"),
        func.count(SensorData.metric_value).label("count"),
    ).join(Device, SensorData.device_id == Device.id)

    stmt = stmt.where(
        SensorData.collected_at >= start_time,
        SensorData.collected_at <= end_time,
    )
    if greenhouse_id:
        stmt = stmt.where(Device.greenhouse_id == greenhouse_id)
    if metric_type:
        stmt = stmt.where(SensorData.metric_type == metric_type)

    stmt = stmt.group_by(interval_expr).order_by(interval_expr)
    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "time_bucket": r.time_bucket.isoformat() if r.time_bucket else None,
            "avg_value": round(r.avg_value, 2) if r.avg_value else None,
            "min_value": r.min_value,
            "max_value": r.max_value,
            "count": r.count,
        }
        for r in rows
    ]


@router.get("/export")
async def export_sensor_data_csv(
    start_time: datetime = Query(..., description="查询起始时间"),
    end_time: datetime = Query(..., description="查询结束时间"),
    greenhouse_id: str | None = Query(None, description="大棚编号"),
    metric_type: str | None = Query(None, description="指标类型"),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    stmt = select(
        Device.device_code,
        Device.greenhouse_id,
        SensorData.metric_type,
        SensorData.metric_value,
        SensorData.collected_at,
    ).join(Device, SensorData.device_id == Device.id)

    stmt = stmt.where(
        SensorData.collected_at >= start_time,
        SensorData.collected_at <= end_time,
    )
    if greenhouse_id:
        stmt = stmt.where(Device.greenhouse_id == greenhouse_id)
    if metric_type:
        stmt = stmt.where(SensorData.metric_type == metric_type)

    stmt = stmt.order_by(SensorData.collected_at.asc())
    result = await db.execute(stmt)
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["设备编码", "大棚编号", "指标类型", "指标值", "采集时间"])
    for r in rows:
        writer.writerow([
            r.device_code,
            r.greenhouse_id,
            r.metric_type,
            r.metric_value,
            r.collected_at.isoformat() if r.collected_at else "",
        ])

    output.seek(0)
    filename = f"sensor_data_{start_time.strftime('%Y%m%d')}_{end_time.strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
