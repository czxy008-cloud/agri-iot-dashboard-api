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
            logger.warning("未知的 metric_type: %s", metric_type)
            return None

        value = data.get("value")
        if value is None:
            logger.warning("payload 缺少 value 字段")
            return None

        collected_at_str = data.get("collected_at")
        if collected_at_str:
            collected_at = datetime.fromisoformat(collected_at_str.replace("Z", "+00:00"))
        else:
            collected_at = datetime.now(timezone.utc)

        return {
            "device_code": device_code,
            "metric_type": metric_type,
            "metric_value": float(value),
            "collected_at": collected_at,
        }
    except (json.JSONDecodeError, ValueError, AttributeError) as e:
        logger.error("MQTT payload 解析失败: %s", e)
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
        if self._on_message_callback:
            parsed = parse_mqtt_payload(topic, payload)
            if parsed:
                self._on_message_callback(parsed)


mqtt_manager = MQTTManager()
