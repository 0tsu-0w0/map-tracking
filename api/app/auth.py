"""パスワード(Argon2)と Cookie セッション。"""
import asyncio
import hashlib
import hmac
import json
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


# Argon2 は 1 回で約 64 MiB のメモリと数十ミリ秒の CPU を使う。
# イベントループを止めないよう別スレッドで動かし、同時に動かす数も絞る(大量のログイン試行でメモリを使い切らないため)
_HASH_SLOTS = asyncio.Semaphore(2)


def hash_password(password: str) -> str:
    """同期版(管理コマンド用)。API からは hash_password_async を使う。"""
    return _ph.hash(password)


def _verify_sync(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def verify_password(password_hash: str | None, password: str) -> bool:
    """同期版(テスト・管理コマンド用)。"""
    return _verify_sync(password_hash, password)


async def hash_password_async(password: str) -> str:
    async with _HASH_SLOTS:
        return await asyncio.to_thread(_ph.hash, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    async with _HASH_SLOTS:
        return await asyncio.to_thread(_verify_sync, password_hash, password)


class CredentialCache:
    """OwnTracks の HTTP モードは位置を送るたびに Basic 認証するため、
    一度確かめた「名前 + パスワード + 保存中のハッシュ」の組を短時間だけ覚えて Argon2 を省く。
    パスワードそのものは保存せず、ランダムな鍵での HMAC だけを持つ。"""

    def __init__(self, ttl_s: float = 600, max_entries: int = 1000) -> None:
        self.ttl_s, self.max_entries = ttl_s, max_entries
        self._key = secrets.token_bytes(32)
        self._entries: dict[bytes, float] = {}

    def _digest(self, name: str, password: str, password_hash: str) -> bytes:
        msg = json.dumps([name, password, password_hash]).encode()
        return hmac.new(self._key, msg, hashlib.sha256).digest()

    def hit(self, name: str, password: str, password_hash: str) -> bool:
        d = self._digest(name, password, password_hash)
        exp = self._entries.get(d)
        if exp is None:
            return False
        if exp < time.monotonic():
            self._entries.pop(d, None)
            return False
        return True

    def add(self, name: str, password: str, password_hash: str) -> None:
        if len(self._entries) >= self.max_entries:
            now = time.monotonic()
            self._entries = {k: v for k, v in self._entries.items() if v > now}
            if len(self._entries) >= self.max_entries:
                self._entries.clear()
        self._entries[self._digest(name, password, password_hash)] = time.monotonic() + self.ttl_s


credential_cache = CredentialCache()


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
    """認証失敗が続いたら一時的に断る(総当たり対策)。

    接続元 IP ごとと、狙われた利用者名ごとの両方で数える(多数の IP から 1 人を狙う攻撃も止める)。"""

    def __init__(self, limit: int = 10, window_s: float = 900, max_keys: int = 10000) -> None:
        self.limit, self.window_s, self.max_keys = limit, window_s, max_keys
        self._fails: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> int:
        """窓の外の記録を捨て、残りの件数を返す。空になったキーは消す。"""
        q = self._fails.get(key)
        if q is None:
            return 0
        cutoff = time.monotonic() - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if not q:
            del self._fails[key]
        return len(q)

    def check(self, *keys: str) -> None:
        if any(self._recent(k) >= self.limit for k in keys):
            raise HTTPException(429, "失敗が続いたため、しばらく待ってから試してください")

    def fail(self, *keys: str) -> None:
        # キーが増えすぎたら期限切れを掃除する(大量の IP・名前でメモリを使い切らせない)
        if len(self._fails) >= self.max_keys:
            for k in list(self._fails):
                self._recent(k)
            if len(self._fails) >= self.max_keys:
                self._fails.clear()
        now = time.monotonic()
        for key in keys:
            self._fails[key].append(now)

    def reset(self, *keys: str) -> None:
        for key in keys:
            self._fails.pop(key, None)


limiter = FailureLimiter()


def client_key(request: Request) -> str:
    return "ip:" + (request.client.host if request.client else "unknown")


def user_key(name: str) -> str:
    return "user:" + name
