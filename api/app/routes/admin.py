"""利用者管理(F-10)。管理者だけが使える。"""
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import hash_password_async, require_admin
from ..owntracks import USER_NAME_RE

router = APIRouter(prefix="/api/admin")


class UserCreate(BaseModel):
    name: str = Field(max_length=64)
    password: str | None = Field(None, min_length=8, max_length=1024)
    role: Literal["admin", "member"] = "member"


class UserPatch(BaseModel):
    password: str | None = Field(None, min_length=8, max_length=1024)
    role: Literal["admin", "member"] | None = None
    disabled: bool | None = None


@router.get("/users")
async def list_users(_=Depends(require_admin)):
    rows = await db.get_pool().fetch(
        """
        SELECT u.id, u.name, u.role, u.disabled, u.password_hash IS NOT NULL AS has_password, u.created_at,
               count(d.id) AS devices, max(d.last_seen_at) AS last_seen_at
        FROM users u LEFT JOIN devices d ON d.user_id = u.id
        GROUP BY u.id ORDER BY u.name
        """
    )
    return [
        {
            **{k: r[k] for k in ("id", "name", "role", "disabled", "has_password", "devices")},
            "created_at": r["created_at"].isoformat(),
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        }
        for r in rows
    ]


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, _=Depends(require_admin)):
    if not USER_NAME_RE.match(body.name):
        raise HTTPException(400, "名前は英数字・_・- だけにしてください(MQTT のユーザー名と同じにします)")
    try:
        uid = await db.get_pool().fetchval(
            "INSERT INTO users (name, password_hash, role) VALUES ($1, $2, $3) RETURNING id",
            body.name,
            await hash_password_async(body.password) if body.password else None,
            body.role,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "同じ名前の利用者がいます") from None
    return {"id": uid}


@router.patch("/users/{user_id}", status_code=204)
async def update_user(user_id: int, body: UserPatch, admin=Depends(require_admin)):
    if user_id == admin["id"] and (body.role == "member" or body.disabled):
        raise HTTPException(400, "自分自身の管理者権限は外せません")
    new_hash = await hash_password_async(body.password) if body.password is not None else None
    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        exists = await conn.fetchval("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", user_id)
        if not exists:
            raise HTTPException(404, "利用者が見つかりません")
        if body.password is not None:
            await conn.execute("UPDATE users SET password_hash = $2 WHERE id = $1", user_id, new_hash)
        if body.role is not None:
            await conn.execute("UPDATE users SET role = $2 WHERE id = $1", user_id, body.role)
        if body.disabled is not None:
            await conn.execute("UPDATE users SET disabled = $2 WHERE id = $1", user_id, body.disabled)
        if body.password is not None or body.disabled:
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin=Depends(require_admin)):
    """利用者と、その端末・履歴・共有設定をすべて消す。"""
    if user_id == admin["id"]:
        raise HTTPException(400, "自分自身は削除できません")
    status = await db.get_pool().execute("DELETE FROM users WHERE id = $1", user_id)
    if status.endswith(" 0"):
        raise HTTPException(404, "利用者が見つかりません")
