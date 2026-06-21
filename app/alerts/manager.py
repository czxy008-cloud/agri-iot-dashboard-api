import asyncio
import logging
from datetime import datetime, timezone

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import AlertLog, AlertRule, Device, SensorData

logger = logging.getLogger(__name__)

OPERATOR_MAP = {
    "gt": lambda v, t: v > t,
    "lt": lambda v, t: v < t,
    "gte": lambda v, t: v >= t,
    "lte": lambda v, t: v <= t,
    "eq": lambda v, t: v == t,
}


class WebSocketManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self._connections.append(websocket)
        logger.info("WebSocket 客户端已连接，当前连接数: %d", len(self._connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self._connections:
            self._connections.remove(websocket)
        logger.info("WebSocket 客户端已断开，当前连接数: %d", len(self._connections))

    async def broadcast(self, message: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WebSocketManager()


async def check_alerts(parsed_data: dict):
    """
    收到传感器数据后，检查是否有告警规则被触发。
    如果触发，写入 alert_logs 并通过 WebSocket 推送。
    """
    device_code = parsed_data["device_code"]
    metric_type = parsed_data["metric_type"]
    metric_value = parsed_data["metric_value"]
    collected_at = parsed_data["collected_at"]

    async with async_session_factory() as db:
        result = await db.execute(
            select(Device).where(Device.device_code == device_code)
        )
        device = result.scalar_one_or_none()
        if not device:
            logger.warning("告警检查: 设备 %s 不存在", device_code)
            return

        stmt = select(AlertRule).where(
            AlertRule.metric_type == metric_type,
            AlertRule.enabled.is_(True),
        )
        rules_result = await db.execute(stmt)
        rules = rules_result.scalars().all()

        for rule in rules:
            if rule.greenhouse_id is not None and rule.greenhouse_id != device.greenhouse_id:
                continue

            compare_fn = OPERATOR_MAP.get(rule.operator)
            if not compare_fn:
                continue

            if compare_fn(metric_value, rule.threshold_value):
                alert_msg = (
                    f"[告警] {device.greenhouse_id} / {device.device_name} "
                    f"{metric_type}={metric_value} 触发规则: "
                    f"{rule.rule_name} (阈值{rule.operator} {rule.threshold_value})"
                )

                log_entry = AlertLog(
                    device_id=device.id,
                    rule_id=rule.id,
                    alert_message=alert_msg,
                    metric_value=metric_value,
                    threshold_value=rule.threshold_value,
                    alert_time=datetime.now(timezone.utc),
                )
                db.add(log_entry)
                await db.commit()

                await ws_manager.broadcast({
                    "type": "alert",
                    "device_id": str(device.id),
                    "device_code": device_code,
                    "greenhouse_id": device.greenhouse_id,
                    "metric_type": metric_type,
                    "metric_value": metric_value,
                    "threshold_value": rule.threshold_value,
                    "operator": rule.operator,
                    "rule_name": rule.rule_name,
                    "message": alert_msg,
                    "alert_time": datetime.now(timezone.utc).isoformat(),
                })
