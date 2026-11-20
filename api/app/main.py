"""FastAPI アプリ本体。API と地図画面(ビルド済みの静的ファイル)を 1 プロセスで配る。"""
import asyncio
import contextlib
import logging
import mimetypes
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, mqtt, tasks
from .auth import hash_password_async
from .config import settings
from .hub import Hub
from .routes import admin, auth, devices, places, pub, shares, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
# 地図の worker(.mjs)を JavaScript として配る(環境によっては未登録)
mimetypes.add_type("text/javascript", ".mjs")


async def bootstrap_admin() -> None:
    """INITIAL_ADMIN_USER / INITIAL_ADMIN_PASS があれば、最初の管理者を作る(既にパスワードがあれば何もしない)。"""
    name, password = settings.initial_admin_user, settings.initial_admin_pass
    if not (name and password):
        return
    status = await db.get_pool().execute(
        """
        INSERT INTO users (name, password_hash, role) VALUES ($1, $2, 'admin')
        ON CONFLICT (name) DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin'
        WHERE users.password_hash IS NULL
        """,
        name,
        await hash_password_async(password),
    )
    if not status.endswith(" 0"):
        log.info("管理者 %s を作成しました", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect(settings.database_url)
    await bootstrap_admin()
    app.state.hub = Hub()
    app.state.mqtt = mqtt.MqttState()
    background = [
        asyncio.create_task(mqtt.run(settings, app.state.hub, app.state.mqtt)),
        asyncio.create_task(tasks.retention_loop(settings)),
        asyncio.create_task(tasks.stale_alert_loop(settings)),
    ]
    try:
        yield
    finally:
        for t in background:
            t.cancel()
        for t in background:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await db.close()


app = FastAPI(title="map-tracking", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """書き換え系の API には X-Requested-With を必須にする。

    独自ヘッダーは別サイトのフォームや単純なリクエストでは付けられないため、CSRF を防げる。
    OwnTracks からの /api/pub は Basic 認証なので対象外。
    """
    path = request.url.path
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and path.startswith("/api/")
        and path != "/api/pub"
        and request.headers.get("x-requested-with") != "fetch"
    ):
        return JSONResponse({"detail": "X-Requested-With: fetch が必要です"}, status_code=403)
    response = await call_next(request)
    if path.startswith("/api/"):
        # 位置情報をブラウザや途中のキャッシュに残さない
        response.headers["Cache-Control"] = "no-store"
    return response


for r in (auth.router, devices.router, places.router, admin.router, shares.router, pub.router, ws.router):
    app.include_router(r)


@app.get("/api/config")
async def config():
    return {"map_style_url": settings.map_style_url or None, "retention_days": settings.retention_days, "tz": settings.tz.key}


@app.get("/healthz")
async def healthz(request: Request):
    await db.get_pool().fetchval("SELECT 1")
    state: mqtt.MqttState = request.app.state.mqtt
    return {
        "db": "ok",
        "mqtt": "connected" if state.connected else ("disabled" if not settings.mqtt_host else "disconnected"),
        "last_message_at": state.last_message_at.isoformat() if state.last_message_at else None,
        "now": datetime.now(timezone.utc).isoformat(),
    }


if Path(settings.static_dir).is_dir():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")
