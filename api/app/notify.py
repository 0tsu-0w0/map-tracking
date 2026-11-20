"""Webhook 通知(Slack / Discord 形式)。受信処理を待たせないよう、送信は裏で行う。"""
import asyncio
import json
import logging
import urllib.request

from .config import settings

log = logging.getLogger(__name__)
_pending: set[asyncio.Task] = set()


def _post(url: str, text: str) -> None:
    # Slack(text)と Discord(content)のどちらの形式でも読めるよう両方入れる
    body = json.dumps({"text": text, "content": text}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10):
        pass


async def send(text: str, url: str | None = None) -> None:
    url = url or settings.alert_webhook_url
    if not url:
        return
    try:
        await asyncio.to_thread(_post, url, text)
    except Exception as e:  # 通知の失敗で本処理は止めない
        log.warning("Webhook 通知に失敗しました: %s", e)


def send_later(text: str) -> None:
    if not settings.alert_webhook_url:
        return
    task = asyncio.create_task(send(text))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
