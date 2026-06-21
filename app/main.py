import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select

from app.alerts.manager import check_alerts
from app.auth.jwt_middleware import get_password_hash
from app.config import settings
from app.database import async_session_factory
from app.devices.mqtt_client import mqtt_manager
from app.models import Device, SensorData, User

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
_INSECURE_JWT_KEY = "change-this-to-a-secure-random-key-in-production"


def _check_jwt_security():
    """
    检查 JWT 密钥安全配置：
    - 如果仍是默认的占位密钥，输出强烈警告。
    - 建议用户通过 .env 或环境变量 JWT_SECRET_KEY 覆盖。
    """
    if settings.JWT_SECRET_KEY == _INSECURE_JWT_KEY:
        border = "!" * 72
        logger.critical(border)
        logger.critical("  【安全警告】 JWT_SECRET_KEY 仍使用默认占位密钥！")
        logger.critical("  任何拿到源码的人都可伪造 token 绕过权限校验，")
        logger.critical("  直接访问所有受保护的设备管理等敏感接口！")
        logger.critical("  ")
        logger.critical("  请立即通过环境变量或 .env 文件设置：")
        logger.critical('    JWT_SECRET_KEY="<请替换为长度>=32的高强度随机字符串>"')
        logger.critical("  ")
        logger.critical("  示例 (PowerShell 生成随机密钥):")
        logger.critical('    $bytes = New-Object byte[] 32;')
        logger.critical('    [Security.Cryptography.RNGCryptoServiceProvider]::Create().GetBytes($bytes);')
        logger.critical('    [Convert]::ToBase64String($bytes)')
        logger.critical(border)


async def _init_default_user():
    """
    应用启动时检查并创建默认管理员用户（如果不存在）。
    默认账号: admin / admin123
    生产环境请立即修改默认密码！
    """
    try:
        async with async_session_factory() as db:
            result = await db.execute(select(User).limit(1))
            existing = result.scalar_one_or_none()

            if existing:
                logger.info("系统已有用户，跳过默认管理员创建")
                return

            hashed_pwd = get_password_hash(DEFAULT_ADMIN_PASSWORD)
            user = User(
                username=DEFAULT_ADMIN_USERNAME,
                hashed_password=hashed_pwd,
                is_active=True,
            )
            db.add(user)
            await db.commit()
            logger.info("=" * 60)
            logger.info("默认管理员用户已创建！")
            logger.info(f"  用户名: {DEFAULT_ADMIN_USERNAME}")
            logger.info(f"  密码: {DEFAULT_ADMIN_PASSWORD}")
            logger.info("  警告: 请在首次登录后立即修改默认密码！")
            logger.info("=" * 60)
    except Exception:
        logger.warning("无法初始化默认用户（数据库可能未就绪），请手动运行 scripts/init_default_user.py")


async def _on_mqtt_message(parsed_data: dict):
    """
    MQTT 消息回调：将解析后的传感器数据写入数据库，并检查告警规则。
    """
    device_code = parsed_data.get("device_code", "?")
    metric_type = parsed_data.get("metric_type", "?")
    metric_value = parsed_data.get("metric_value", "?")

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(Device).where(Device.device_code == parsed_data["device_code"])
            )
            device = result.scalar_one_or_none()
            if not device:
                logger.error(
                    "数据入库失败: 设备 %s 未在系统中登记，数据已丢弃！"
                    " 请先通过 POST /api/devices 注册该设备。"
                    " (metric=%s, value=%s)",
                    parsed_data["device_code"], metric_type, metric_value,
                )
                return

            sensor_record = SensorData(
                device_id=device.id,
                metric_type=parsed_data["metric_type"],
                metric_value=parsed_data["metric_value"],
                collected_at=parsed_data["collected_at"],
            )
            db.add(sensor_record)
            await db.commit()

        logger.info(
            "数据入库成功 device=%s metric=%s value=%s",
            device_code, metric_type, metric_value,
        )

        await check_alerts(parsed_data)
    except Exception:
        logger.exception(
            "处理 MQTT 消息异常 device=%s metric=%s value=%s",
            device_code, metric_type, metric_value,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_jwt_security()

    await _init_default_user()

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
