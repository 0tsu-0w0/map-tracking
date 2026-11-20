"""OwnTracks の HTTP モード受信(F-02)。Web と同じ名前・パスワードの Basic 認証。"""
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .. import db
from ..auth import client_key, limiter, verify_password
from ..ingest import save_location
from ..owntracks import parse_location, valid_name

router = APIRouter()
basic = HTTPBasic(realm="map-tracking")


@router.post("/api/pub")
async def pub(
    request: Request,
    creds: HTTPBasicCredentials = Depends(basic),
    x_limit_u: str | None = Header(None),
    x_limit_d: str | None = Header(None),
):
    key = client_key(request)
    limiter.check(key)
    pool = db.get_pool()
    user = await pool.fetchrow("SELECT password_hash, disabled FROM users WHERE name = $1", creds.username)
    if not verify_password(user["password_hash"] if user else None, creds.password) or user is None or user["disabled"]:
        limiter.fail(key)
        raise HTTPException(401, "認証に失敗しました", headers={"WWW-Authenticate": 'Basic realm="map-tracking"'})
    limiter.reset(key)
    if x_limit_u and x_limit_u != creds.username:
        raise HTTPException(403, "X-Limit-U がログイン名と違います")
    device = x_limit_d or "http"
    if not valid_name(device):
        raise HTTPException(400, "端末名(X-Limit-D)が不正です")
    try:
        payload = json.loads(await request.body())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(400, "JSON を送ってください") from None
    loc = parse_location(payload)
    if loc is not None:
        async with pool.acquire() as conn:
            event = await save_location(conn, creds.username, device, loc)
        if event:
            await request.app.state.hub.broadcast(pool, event)
    # OwnTracks は応答として配列(他の人の位置など)を期待する。何も返さないので空配列
    return []
