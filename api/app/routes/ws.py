"""新着位置のプッシュ配信(F-06)。"""
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
    await websocket.accept()
    hub = websocket.app.state.hub
    client = Client(ws=websocket, user_id=user["id"])
    hub.add(client)
    try:
        while True:
            await websocket.receive_text()  # 受信内容は使わない(切断の検知用)
    except WebSocketDisconnect:
        pass
    finally:
        hub.discard(client)
