"""Mosquitto を購読して位置を保存する。切れたら待ち時間を伸ばしながら再接続する。"""
import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import aiomqtt

from . import db
from .config import Settings
from .hub import Hub
from .ingest import ingest_payload
from .owntracks import parse_topic

log = logging.getLogger(__name__)
MAX_PAYLOAD = 64 * 1024


@dataclass
class MqttState:
    connected: bool = False
    last_message_at: datetime | None = None


async def handle_message(hub: Hub, state: MqttState, topic: str, payload: bytes) -> list[dict]:
    state.last_message_at = datetime.now(timezone.utc)
    names = parse_topic(topic)
    if names is None or len(payload) > MAX_PAYLOAD:
        return []
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, ValueError, RecursionError):
        log.warning("JSON でないメッセージを無視しました: %s", topic)
        return []
    return await ingest_payload(db.get_pool(), hub, names[0], names[1], data)


async def run(settings: Settings, hub: Hub, state: MqttState) -> None:
    if not settings.mqtt_host:
        log.info("MQTT_HOST が空なので MQTT は購読しません")
        return
    delay = 1
    while True:
        try:
            # clean_session=False + QoS 1: API が止まっている間の位置もブローカーが溜めておく
            async with aiomqtt.Client(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                username=settings.mqtt_user or None,
                password=settings.mqtt_pass or None,
                identifier="map-tracking-api",
                clean_session=False,
            ) as client:
                for topic in settings.mqtt_topics:
                    await client.subscribe(topic, qos=1)
                state.connected = True
                delay = 1
                log.info("MQTT に接続しました (%s を購読)", ", ".join(settings.mqtt_topics))
                async for message in client.messages:
                    try:
                        await handle_message(hub, state, message.topic.value, bytes(message.payload))
                    except Exception:
                        log.exception("位置の保存に失敗しました: %s", message.topic.value)
        except aiomqtt.MqttError as e:
            state.connected = False
            log.warning("MQTT 接続エラー: %s (%d 秒後に再接続)", e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
