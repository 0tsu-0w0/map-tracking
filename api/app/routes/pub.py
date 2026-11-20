"""OwnTracks の HTTP モード受信(F-02)。Web と同じ名前・パスワードの Basic 認証。"""
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .. import db
from ..auth import client_key, credential_cache, limiter, user_key, verify_password_async
from ..ingest import ingest_payload
from ..owntracks import valid_name

router = APIRouter()
basic = HTTPBasic(realm="map-tracking")
MAX_BODY = 64 * 1024


@router.post("/api/pub")
async def pub(
    request: Request,
    creds: HTTPBasicCredentials = Depends(basic),
    x_limit_u: str | None = Header(None),
    x_limit_d: str | None = Header(None),
):
    keys = (client_key(request), user_key(creds.username))
    limiter.check(*keys)
    pool = db.get_pool()
    user = await pool.fetchrow("SELECT password_hash, disabled FROM users WHERE name = $1", creds.username)
    pw_hash = user["password_hash"] if user else None
    ok = bool(pw_hash) and credential_cache.hit(creds.username, creds.password, pw_hash)
    if not ok:
        ok = await verify_password_async(pw_hash, creds.password)
        if ok:
            credential_cache.add(creds.username, creds.password, pw_hash)
    if not ok or user is None or user["disabled"]:
        limiter.fail(*keys)
        raise HTTPException(401, "認証に失敗しました", headers={"WWW-Authenticate": 'Basic realm="map-tracking"'})
    limiter.reset(*keys)
    if x_limit_u and x_limit_u != creds.username:
        raise HTTPException(403, "X-Limit-U がログイン名と違います")
    device = x_limit_d or "http"
    if not valid_name(device):
        raise HTTPException(400, "端末名(X-Limit-D)が不正です")
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "大きすぎます")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise HTTPException(400, "JSON を送ってください") from None
    await ingest_payload(pool, request.app.state.hub, creds.username, device, payload)
    # OwnTracks は応答として配列(他の人の位置など)を期待する。何も返さないので空配列
    return []
