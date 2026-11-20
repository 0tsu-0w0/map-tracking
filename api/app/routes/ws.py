"""新着位置・出入りのプッシュ配信(F-06)。"""
import asyncio
import contextlib
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db
from ..auth import SESSION_COOKIE, user_from_token
from ..hub import Client

router = APIRouter()


@router.websocket("/api/ws")
async def ws(websocket: WebSocket):
    # 別サイトのページから Cookie 付きで接続されるのを防ぐ
    origin = websocket.headers.get("origin")
    if origin and urlparse(origin).netloc != websocket.headers.get("host"):
        await websocket.close(code=1008)
        return
    user = await user_from_token(db.get_pool(), websocket.cookies.get(SESSION_COOKIE))
    if user is None:
        await websocket.close(code=1008)
        return
    hub = websocket.app.state.hub
    if not hub.can_add(user["id"]):
        await websocket.close(code=1013)
        return
    await websocket.accept()
    client = Client(ws=websocket, user_id=user["id"])
    hub.add(client)
    sender = asyncio.create_task(client.sender())
    receiver = asyncio.create_task(_drain(websocket))
    try:
        # どちらかが終わったら(切断・送信の失敗)両方止める
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        hub.discard(client)
        for t in (sender, receiver):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        with contextlib.suppress(Exception):
            await websocket.close()


async def _drain(websocket: WebSocket) -> None:
    """受信内容は使わない(切断の検知用)。"""
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            await websocket.receive_text()
