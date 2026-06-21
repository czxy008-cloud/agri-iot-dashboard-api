import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from app.alerts.manager import check_alerts
from app.database import async_session_factory
from app.devices.mqtt_client import mqtt_manager
from app.models import Device, SensorData

logger = logging.getLogger(__name__)


async def _on_mqtt_message(parsed_data: dict):
    """
    MQTT 消息回调：将解析后的传感器数据写入数据库，并检查告警规则。
    """
    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Device).where(Device.device_code == parsed_data["device_code"])
            )
            device = result.scalar_one_or_none()
            if not device:
                logger.warning("收到未知设备 %s 的数据，已忽略", parsed_data["device_code"])
                return

            sensor_record = SensorData(
                device_id=device.id,
                metric_type=parsed_data["metric_type"],
                metric_value=parsed_data["metric_value"],
                collected_at=parsed_data["collected_at"],
            )
            db.add(sensor_record)
            await db.commit()

        await check_alerts(parsed_data)
    except Exception:
        logger.exception("处理 MQTT 消息时发生错误")


@asynccontextmanager
async def lifespan(app: FastAPI):
    mqtt_manager.set_message_callback(_on_mqtt_message)
    try:
        await mqtt_manager.start()
    except Exception:
        logger.warning("MQTT 连接失败，将在无 MQTT 模式下运行")

    yield

    await mqtt_manager.stop()


app = FastAPI(
    title="农业物联网数据可视化大屏 - 后端服务",
    description="基于 FastAPI 的农业物联网数据采集、告警与查询服务",
    version="1.0.0",
    lifespan=lifespan,
)

from app.alerts.router import router as alerts_router
from app.auth.router import router as auth_router
from app.devices.router import router as devices_router
from app.history.router import router as history_router

app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(alerts_router)
app.include_router(history_router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
