"""パスワード(Argon2)と Cookie セッション。"""
import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request

from . import db
from .config import settings

SESSION_COOKIE = "ot_session"
_ph = PasswordHasher()
# 存在しない利用者でも同じだけ時間をかけ、名前の有無を応答時間から分からなくする
_DUMMY_HASH = _ph.hash(secrets.token_hex(16))


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(pool: asyncpg.Pool, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    await pool.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ($1, $2, now() + $3::interval)",
        _token_hash(token),
        user_id,
        timedelta(days=settings.session_days),
    )
    return token


async def delete_session(pool: asyncpg.Pool, token: str) -> None:
    await pool.execute("DELETE FROM sessions WHERE token_hash = $1", _token_hash(token))


async def user_from_token(pool: asyncpg.Pool, token: str | None) -> asyncpg.Record | None:
    if not token:
        return None
    return await pool.fetchrow(
        """
        SELECT u.id, u.name, u.role FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = $1 AND s.expires_at > now() AND NOT u.disabled
        """,
        _token_hash(token),
    )


async def current_user(request: Request) -> asyncpg.Record:
    user = await user_from_token(db.get_pool(), request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(401, "ログインしてください")
    return user


async def require_admin(user: asyncpg.Record = Depends(current_user)) -> asyncpg.Record:
    if user["role"] != "admin":
        raise HTTPException(403, "管理者だけが使えます")
    return user


class FailureLimiter:
    """同じ接続元からの認証失敗が続いたら一時的に断る(総当たり対策)。"""

    def __init__(self, limit: int = 10, window_s: float = 900) -> None:
        self.limit, self.window_s = limit, window_s
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str) -> deque[float]:
        q = self._fails[key]
        cutoff = time.monotonic() - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def check(self, key: str) -> None:
        if len(self._prune(key)) >= self.limit:
            raise HTTPException(429, "失敗が続いたため、しばらく待ってから試してください")

    def fail(self, key: str) -> None:
        self._prune(key).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._fails.pop(key, None)


limiter = FailureLimiter()


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"
