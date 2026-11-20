"""共有設定(F-13): 自分の位置を誰に見せるか。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import current_user

router = APIRouter(prefix="/api/shares")


class ShareIn(BaseModel):
    viewer: str = Field(max_length=64)


@router.get("")
async def list_shares(user=Depends(current_user)):
    pool = db.get_pool()
    viewers = await pool.fetch(
        "SELECT u.id, u.name FROM shares s JOIN users u ON u.id = s.viewer_user_id WHERE s.owner_user_id = $1 ORDER BY u.name",
        user["id"],
    )
    owners = await pool.fetch(
        "SELECT u.id, u.name FROM shares s JOIN users u ON u.id = s.owner_user_id WHERE s.viewer_user_id = $1 ORDER BY u.name",
        user["id"],
    )
    return {"viewers": [dict(r) for r in viewers], "shared_with_me": [dict(r) for r in owners]}


@router.post("", status_code=201)
async def add_share(body: ShareIn, user=Depends(current_user)):
    pool = db.get_pool()
    viewer_id = await pool.fetchval("SELECT id FROM users WHERE name = $1 AND NOT disabled", body.viewer)
    if viewer_id is None:
        raise HTTPException(404, "その名前の利用者はいません")
    if viewer_id == user["id"]:
        raise HTTPException(400, "自分自身には共有できません")
    await pool.execute(
        "INSERT INTO shares (owner_user_id, viewer_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", user["id"], viewer_id
    )
    return {"id": viewer_id, "name": body.viewer}


@router.delete("/{viewer_id}", status_code=204)
async def remove_share(viewer_id: int, user=Depends(current_user)):
    await db.get_pool().execute(
        "DELETE FROM shares WHERE owner_user_id = $1 AND viewer_user_id = $2", user["id"], viewer_id
    )


@router.delete("/from/{owner_id}", status_code=204)
async def leave_share(owner_id: int, user=Depends(current_user)):
    """自分に共有されている相手を一覧から外す。"""
    await db.get_pool().execute(
        "DELETE FROM shares WHERE owner_user_id = $1 AND viewer_user_id = $2", owner_id, user["id"]
    )
