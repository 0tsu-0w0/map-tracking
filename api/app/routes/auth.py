from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import db
from ..auth import (
    SESSION_COOKIE,
    client_key,
    create_session,
    current_user,
    delete_session,
    hash_password_async,
    limiter,
    user_key,
    verify_password_async,
)
from ..config import settings

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    name: str = Field(max_length=64)
    password: str = Field(max_length=1024)


class EncKeyIn(BaseModel):
    key: str | None = Field(None, min_length=1, max_length=256)


class PasswordIn(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(min_length=8, max_length=1024)


def _me(user, has_enc_key: bool = False) -> dict:
    return {"id": user["id"], "name": user["name"], "role": user["role"], "has_enc_key": has_enc_key}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    keys = (client_key(request), user_key(body.name))
    limiter.check(*keys)
    pool = db.get_pool()
    user = await pool.fetchrow(
        "SELECT id, name, role, password_hash, disabled FROM users WHERE name = $1", body.name
    )
    ok = await verify_password_async(user["password_hash"] if user else None, body.password)
    if not ok or user is None or user["disabled"]:
        limiter.fail(*keys)
        raise HTTPException(401, "名前かパスワードが違います")
    limiter.reset(*keys)
    token = await create_session(pool, user["id"])
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return _me(user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await delete_session(db.get_pool(), token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(user=Depends(current_user)):
    has_key = await db.get_pool().fetchval("SELECT enc_key IS NOT NULL FROM users WHERE id = $1", user["id"])
    return _me(user, bool(has_key))


@router.put("/encryption-key", status_code=204)
async def set_encryption_key(body: EncKeyIn, user=Depends(current_user)):
    """OwnTracks の「Encryption Key」と同じ文字列を登録する(null で解除)。鍵は画面に返さない。"""
    await db.get_pool().execute("UPDATE users SET enc_key = $2 WHERE id = $1", user["id"], body.key)


@router.post("/password", status_code=204)
async def change_password(body: PasswordIn, request: Request, user=Depends(current_user)):
    pool = db.get_pool()
    keys = (client_key(request), user_key(user["name"]))
    limiter.check(*keys)
    current_hash = await pool.fetchval("SELECT password_hash FROM users WHERE id = $1", user["id"])
    if not await verify_password_async(current_hash, body.current):
        limiter.fail(*keys)
        raise HTTPException(400, "今のパスワードが違います")
    new_hash = await hash_password_async(body.new)
    token = request.cookies.get(SESSION_COOKIE)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("UPDATE users SET password_hash = $2 WHERE id = $1", user["id"], new_hash)
        # 他の端末のログインは切る(今のブラウザは残す)
        await conn.execute(
            "DELETE FROM sessions WHERE user_id = $1 AND token_hash <> encode(sha256($2::bytea), 'hex')",
            user["id"],
            (token or "").encode(),
        )
