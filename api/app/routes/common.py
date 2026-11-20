"""ルート間で使う共通処理(端末の閲覧権限・期間の解釈)。"""
from datetime import datetime

import asyncpg
from fastapi import HTTPException

from ..config import settings


async def device_for(pool: asyncpg.Pool, user: asyncpg.Record, device_id: int, write: bool = False) -> asyncpg.Record:
    """見られない端末は 404(存在を明かさない)。書き換えは本人か管理者だけ。"""
    dev = await pool.fetchrow(
        """
        SELECT d.id, d.name, d.tid, d.user_id, u.name AS user_name
        FROM devices d JOIN users u ON u.id = d.user_id WHERE d.id = $1
        """,
        device_id,
    )
    if dev is None:
        raise HTTPException(404, "端末が見つかりません")
    if dev["user_id"] == user["id"] or user["role"] == "admin":
        return dev
    shared = await pool.fetchval(
        "SELECT 1 FROM shares WHERE owner_user_id = $1 AND viewer_user_id = $2", dev["user_id"], user["id"]
    )
    if not shared:
        raise HTTPException(404, "端末が見つかりません")
    if write:
        raise HTTPException(403, "本人か管理者だけが操作できます")
    return dev


def aware(dt: datetime | None) -> datetime | None:
    """タイムゾーンのない日時は設定の TZ(既定 Asia/Tokyo)として扱う。"""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=settings.tz)
    return dt


def check_range(start: datetime | None, end: datetime | None) -> None:
    if start and end and start >= end:
        raise HTTPException(400, "from は to より前にしてください")
