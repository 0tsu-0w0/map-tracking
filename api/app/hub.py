"""ブラウザへの新着のプッシュ配信(WebSocket)。API は 1 プロセスで動かす前提。

受信処理(MQTT・HTTP)を遅いブラウザに引きずられないよう、相手ごとのキューに積むだけにして、
実際の送信は相手ごとのタスクが行う。キューがあふれた相手は切断する。
"""
import asyncio
import contextlib
import logging
from collections import Counter

import asyncpg
from fastapi import WebSocket

log = logging.getLogger(__name__)

QUEUE_SIZE = 100
MAX_PER_USER = 10


class Client:
    def __init__(self, ws: WebSocket, user_id: int) -> None:
        self.ws = ws
        self.user_id = user_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_SIZE)

    async def sender(self) -> None:
        while True:
            event = await self.queue.get()
            await asyncio.wait_for(self.ws.send_json(event), timeout=10)


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def can_add(self, user_id: int) -> bool:
        return Counter(c.user_id for c in self.clients)[user_id] < MAX_PER_USER

    def add(self, client: Client) -> None:
        self.clients.add(client)

    def discard(self, client: Client) -> None:
        self.clients.discard(client)

    async def broadcast(self, pool: asyncpg.Pool, event: dict, only: set[int] | None = None) -> None:
        """event["user_id"](端末の持ち主)の位置を見られる人に配る。only があればさらにその人たちに絞る。"""
        if not self.clients:
            return
        # 本人・管理者・共有を受けた人だけ(権限は配信のたびに DB で確かめる)
        rows = await pool.fetch(
            """
            SELECT id FROM users
            WHERE NOT disabled AND (
                id = $1 OR role = 'admin'
                OR id IN (SELECT viewer_user_id FROM shares WHERE owner_user_id = $1)
            )
            """,
            event["user_id"],
        )
        allowed = {r["id"] for r in rows}
        if only is not None:
            allowed &= only
        for c in list(self.clients):
            if c.user_id not in allowed:
                continue
            try:
                c.queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("配信が追いつかないため WebSocket を切断します(user_id=%s)", c.user_id)
                self.discard(c)
                with contextlib.suppress(Exception):
                    await c.ws.close(code=1013)
