"""ブラウザへの新着位置のプッシュ配信(WebSocket)。API は 1 プロセスで動かす前提。"""
import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg
from fastapi import WebSocket

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Client:
    ws: WebSocket
    user_id: int
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def add(self, client: Client) -> None:
        self.clients.add(client)

    def discard(self, client: Client) -> None:
        self.clients.discard(client)

    async def broadcast(self, pool: asyncpg.Pool, event: dict) -> None:
        if not self.clients:
            return
        # 本人・管理者・共有を受けた人だけに配る(権限は配信のたびに DB で確かめる)
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
        targets = [c for c in self.clients if c.user_id in allowed]
        await asyncio.gather(*(self._send(c, event) for c in targets))

    async def _send(self, client: Client, event: dict) -> None:
        try:
            async with client.lock:
                await asyncio.wait_for(client.ws.send_json(event), timeout=5)
        except Exception:  # 切断済み・遅すぎる相手は外す
            self.discard(client)
