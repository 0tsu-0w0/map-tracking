"""ジオフェンス(F-14): 場所の登録と、出入りの記録。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..auth import current_user
from .common import aware, check_range, device_for

router = APIRouter()


class PlaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_m: float = Field(150, ge=20, le=50000)
    notify: bool = True


class PlacePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    radius_m: float | None = Field(None, ge=20, le=50000)
    notify: bool | None = None


def _place(r) -> dict:
    return {"id": r["id"], "name": r["name"], "lat": r["lat"], "lon": r["lon"], "radius_m": r["radius_m"], "notify": r["notify"]}


_COLS = "id, name, ST_Y(geom) AS lat, ST_X(geom) AS lon, radius_m, notify"


@router.get("/api/places")
async def list_places(user=Depends(current_user)):
    rows = await db.get_pool().fetch(f"SELECT {_COLS} FROM places WHERE user_id = $1 ORDER BY name", user["id"])
    return [_place(r) for r in rows]


@router.post("/api/places", status_code=201)
async def create_place(body: PlaceIn, user=Depends(current_user)):
    pool = db.get_pool()
    if await pool.fetchval("SELECT count(*) FROM places WHERE user_id = $1", user["id"]) >= 100:
        raise HTTPException(400, "登録できる場所は 1 人 100 か所までです")
    r = await pool.fetchrow(
        f"""
        INSERT INTO places (user_id, name, geom, radius_m, notify)
        VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6) RETURNING {_COLS}
        """,
        user["id"],
        body.name.strip(),
        body.lon,
        body.lat,
        body.radius_m,
        body.notify,
    )
    return _place(r)


@router.patch("/api/places/{place_id}")
async def update_place(place_id: int, body: PlacePatch, user=Depends(current_user)):
    pool = db.get_pool()
    async with pool.acquire() as conn, conn.transaction():
        cur = await conn.fetchrow(f"SELECT {_COLS} FROM places WHERE id = $1 AND user_id = $2 FOR UPDATE", place_id, user["id"])
        if cur is None:
            raise HTTPException(404, "場所が見つかりません")
        lat = body.lat if body.lat is not None else cur["lat"]
        lon = body.lon if body.lon is not None else cur["lon"]
        r = await conn.fetchrow(
            f"""
            UPDATE places SET name = $2, geom = ST_SetSRID(ST_MakePoint($3, $4), 4326), radius_m = $5, notify = $6
            WHERE id = $1 RETURNING {_COLS}
            """,
            place_id,
            (body.name or cur["name"]).strip(),
            lon,
            lat,
            body.radius_m if body.radius_m is not None else cur["radius_m"],
            body.notify if body.notify is not None else cur["notify"],
        )
        if body.lat is not None or body.lon is not None or body.radius_m is not None:
            # 形が変わったら、出入りの状態は次の位置で判定し直す
            await conn.execute("DELETE FROM place_presence WHERE place_id = $1", place_id)
    return _place(r)


@router.delete("/api/places/{place_id}", status_code=204)
async def delete_place(place_id: int, user=Depends(current_user)):
    status = await db.get_pool().execute("DELETE FROM places WHERE id = $1 AND user_id = $2", place_id, user["id"])
    if status.endswith(" 0"):
        raise HTTPException(404, "場所が見つかりません")


@router.get("/api/devices/{device_id}/events")
async def device_events(
    device_id: int,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    limit: int = Query(200, ge=1, le=1000),
    user=Depends(current_user),
):
    """端末の出入りの記録。

    OwnTracks 側の Regions(source=device)は端末を見られる人全員に、
    この API が判定した場所(source=server)は、場所の持ち主・端末の持ち主・管理者にだけ見せる。
    """
    pool = db.get_pool()
    dev = await device_for(pool, user, device_id)
    start, end = aware(start), aware(end)
    check_range(start, end)
    rows = await pool.fetch(
        """
        SELECT place_name, event, source, occurred_at FROM place_events
        WHERE device_id = $1
          AND ($2::timestamptz IS NULL OR occurred_at >= $2) AND ($3::timestamptz IS NULL OR occurred_at < $3)
          AND (source = 'device' OR $5 OR place_owner = $6)
        ORDER BY occurred_at DESC LIMIT $4
        """,
        device_id,
        start,
        end,
        limit,
        user["role"] == "admin" or dev["user_id"] == user["id"],
        user["id"],
    )
    return [
        {"place_name": r["place_name"], "event": r["event"], "source": r["source"], "occurred_at": r["occurred_at"].isoformat()}
        for r in rows
    ]
