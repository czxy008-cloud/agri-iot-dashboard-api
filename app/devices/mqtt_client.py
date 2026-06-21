import asyncio
import json
import logging
from datetime import datetime, timezone

from gmqtt import Client as MQTTClient

from app.config import settings

logger = logging.getLogger(__name__)

VALID_METRIC_TYPES = {"temperature", "humidity", "light", "ph"}


def parse_mqtt_payload(topic: str, payload: bytes) -> dict | None:
    """
    解析 MQTT 消息。

    预期 topic 格式: agri/sensor/{device_code}
    预期 payload 格式 (JSON):
    {
        "metric_type": "temperature",
        "value": 28.5,
        "collected_at": "2025-01-15T10:30:00Z"   // 可选，缺省使用当前时间
    }
    """
    try:
        parts = topic.split("/")
        if len(parts) < 3:
            logger.warning("Topic 格式无法解析: %s", topic)
            return None

        device_code = parts[-1]

        data = json.loads(payload.decode("utf-8"))

        metric_type = data.get("metric_type", "")
        if metric_type not in VALID_METRIC_TYPES:
            logger.warning("未知的 metric_type: %s device=%s", metric_type, device_code)
            return None

        value = data.get("value")
        if value is None:
            logger.warning("payload 缺少 value 字段, device=%s", device_code)
            return None

        collected_at_str = data.get("collected_at")
        if collected_at_str:
            collected_at = datetime.fromisoformat(collected_at_str.replace("Z", "+00:00"))
        else:
            collected_at = datetime.now(timezone.utc)

        parsed = {
            "device_code": device_code,
            "metric_type": metric_type,
            "metric_value": float(value),
            "collected_at": collected_at,
        }
        logger.debug(
            "MQTT 消息解析成功: device=%s, metric=%s, value=%s",
            device_code, metric_type, value,
        )
        return parsed
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        logger.error("MQTT payload 解析失败 topic=%s error=%s payload=%s",
                     topic, e, payload[:200] if len(payload) > 200 else payload)
        return None


class MQTTManager:
    def __init__(self):
        self._client: MQTTClient | None = None
        self._on_message_callback = None

    def set_message_callback(self, callback):
        self._on_message_callback = callback

    async def start(self):
        client_id = f"agri-iot-backend-{id(self)}"
        self._client = MQTTClient(client_id)

        if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
            self._client.set_auth_credentials(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)

        self._client.on_message = self._handle_message

        await self._client.connect(
            host=settings.MQTT_BROKER_HOST,
            port=settings.MQTT_BROKER_PORT,
        )
        self._client.subscribe(settings.MQTT_TOPIC_PREFIX, qos=1)
        logger.info("MQTT 客户端已连接并订阅: %s", settings.MQTT_TOPIC_PREFIX)

    async def stop(self):
        if self._client:
            await self._client.disconnect()
            logger.info("MQTT 客户端已断开")

    def _handle_message(self, client, topic, payload, qos, properties):
        """
        gmqtt 的 on_message 回调是同步的，
        必须通过 asyncio.create_task 把异步回调调度到事件循环，
        否则协程对象不会被执行，数据会直接丢失！
        """
        if self._on_message_callback:
            parsed = parse_mqtt_payload(topic, payload)
            if parsed:
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                task = loop.create_task(self._on_message_callback(parsed))
                task.add_done_callback(self._on_task_done)

    @staticmethod
    def _on_task_done(task):
        """捕获任务中的异常，避免静默丢失。"""
        try:
            task.result()
        except Exception:
            logger.exception("MQTT 消息处理任务异常")


mqtt_manager = MQTTManager()
