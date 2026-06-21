from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.manager import ws_manager
from app.auth.jwt_middleware import get_current_user
from app.database import get_db
from app.models import AlertLog, AlertRule, User

router = APIRouter(prefix="/api/alerts", tags=["告警中心"])


class AlertRuleCreate(BaseModel):
    rule_name: str
    metric_type: str
    operator: str
    threshold_value: float
    greenhouse_id: str | None = None
    enabled: bool = True


class AlertRuleUpdate(BaseModel):
    rule_name: str | None = None
    threshold_value: float | None = None
    operator: str | None = None
    enabled: bool | None = None


class AlertRuleOut(BaseModel):
    id: int
    rule_name: str
    metric_type: str
    operator: str
    threshold_value: float
    greenhouse_id: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AlertLogOut(BaseModel):
    id: int
    device_id: str
    rule_id: int
    alert_message: str
    metric_value: float
    threshold_value: float
    alert_time: datetime
    acknowledged: bool

    model_config = {"from_attributes": True}


@router.post("/rules", response_model=AlertRuleOut, status_code=201)
async def create_alert_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    rule = AlertRule(**body.model_dump())
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


@router.get("/rules", response_model=list[AlertRuleOut])
async def list_alert_rules(
    metric_type: str | None = None,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    stmt = select(AlertRule)
    if metric_type:
        stmt = stmt.where(AlertRule.metric_type == metric_type)
    result = await db.execute(stmt.order_by(AlertRule.id))
    return result.scalars().all()


@router.patch("/rules/{rule_id}", response_model=AlertRuleOut)
async def update_alert_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"告警规则 ID={rule_id} 不存在",
        )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.flush()
    await db.refresh(rule)
    return rule


@router.get("/logs", response_model=list[AlertLogOut])
async def list_alert_logs(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    stmt = select(AlertLog).order_by(AlertLog.alert_time.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.patch("/logs/{log_id}/acknowledge")
async def acknowledge_alert(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(AlertLog).where(AlertLog.id == log_id))
    log_entry = result.scalar_one_or_none()
    if not log_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"告警日志 ID={log_id} 不存在",
        )
    log_entry.acknowledged = True
    await db.flush()
    return {"detail": "已确认告警"}


@router.websocket("/ws")
async def alert_websocket(websocket: WebSocket, token: str | None = None):
    """
    实时告警 WebSocket 接口。
    
    连接方式: ws://host/api/alerts/ws?token=<JWT_TOKEN>
    
    未认证的连接将被直接拒绝，关闭码 1008 (policy violation)。
    """
    from app.auth.jwt_middleware import get_current_user_for_ws

    user = await get_current_user_for_ws(websocket, token)
    if user is None:
        await websocket.close(code=1008, reason="未授权的访问：请通过 query 参数提供有效的 JWT token")
        return

    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
