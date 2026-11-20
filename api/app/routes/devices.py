from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from .. import db, export
from ..auth import current_user
from ..config import settings
from .common import aware, check_range, device_for

router = APIRouter(prefix="/api/devices")

_POINT_COLS = """
    recorded_at, ST_Y(geom) AS lat, ST_X(geom) AS lon, accuracy_m, altitude_m,
    velocity_kmh, battery_pct, conn
"""
_RANGE = "($2::timestamptz IS NULL OR recorded_at >= $2) AND ($3::timestamptz IS NULL OR recorded_at < $3)"


@router.get("")
async def list_devices(user=Depends(current_user)):
    """見られる端末の一覧と最新位置(F-06)。管理者は全員分。"""
    rows = await db.get_pool().fetch(
        f"""
        SELECT d.id, d.name, d.tid, d.user_id, u.name AS user_name, d.last_seen_at,
               l.lat, l.lon, l.accuracy_m, l.battery_pct, l.velocity_kmh, l.recorded_at
        FROM devices d
        JOIN users u ON u.id = d.user_id
        LEFT JOIN LATERAL (
            SELECT {_POINT_COLS} FROM locations WHERE device_id = d.id ORDER BY recorded_at DESC LIMIT 1
        ) l ON true
        WHERE NOT u.disabled AND (
            u.id = $1 OR $2
            OR u.id IN (SELECT owner_user_id FROM shares WHERE viewer_user_id = $1)
        )
        ORDER BY u.name, d.name
        """,
        user["id"],
        user["role"] == "admin",
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "tid": r["tid"],
            "user_id": r["user_id"],
            "user_name": r["user_name"],
            "can_edit": r["user_id"] == user["id"] or user["role"] == "admin",
            "last": None
            if r["lat"] is None
            else {
                "lat": r["lat"],
                "lon": r["lon"],
                "acc": r["accuracy_m"],
                "batt": r["battery_pct"],
                "vel": r["velocity_kmh"],
                "recorded_at": r["recorded_at"].isoformat(),
            },
        }
        for r in rows
    ]


@router.get("/{device_id}/locations")
async def locations(
    device_id: int,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    user=Depends(current_user),
):
    """期間の履歴を GeoJSON で返す(F-07)。既定は直近 24 時間。"""
    pool = db.get_pool()
    await device_for(pool, user, device_id)
    end = aware(end) or datetime.now(timezone.utc)
    start = aware(start) or end - timedelta(hours=24)
    check_range(start, end)
    if end - start > timedelta(days=93):
        raise HTTPException(400, "一度に見られるのは 93 日分までです")
    rows = await pool.fetch(
        f"SELECT {_POINT_COLS} FROM locations WHERE device_id = $1 AND {_RANGE} ORDER BY recorded_at LIMIT $4",
        device_id,
        start,
        end,
        settings.max_points + 1,
    )
    truncated = len(rows) > settings.max_points
    rows = rows[: settings.max_points]
    coords = [[r["lon"], r["lat"]] for r in rows]
    features = []
    if len(coords) >= 2:
        features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {"kind": "track"}})
    for r in rows:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {
                    "kind": "point",
                    "recorded_at": r["recorded_at"].isoformat(),
                    "acc": r["accuracy_m"],
                    "vel": r["velocity_kmh"],
                    "batt": r["battery_pct"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features, "count": len(rows), "truncated": truncated}


@router.get("/{device_id}/export")
async def export_locations(
    device_id: int,
    format: str = Query("gpx", pattern="^(gpx|geojson|csv)$"),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    user=Depends(current_user),
):
    """GPX / GeoJSON / CSV で取り出す(F-11)。期間を省くと全期間。"""
    pool = db.get_pool()
    dev = await device_for(pool, user, device_id)
    start, end = aware(start), aware(end)
    check_range(start, end)
    media_type, ext = export.FORMATS[format]
    title = f"{dev['user_name']}/{dev['name']}"

    async def stream():
        yield export.header(format, title)
        async with pool.acquire() as conn, conn.transaction():
            first = True
            async for r in conn.cursor(
                f"SELECT {_POINT_COLS} FROM locations WHERE device_id = $1 AND {_RANGE} ORDER BY recorded_at",
                device_id,
                start,
                end,
            ):
                yield export.row(format, dict(r), first)
                first = False
        yield export.footer(format)

    filename = f"{dev['user_name']}-{dev['name']}.{ext}".replace('"', "")
    return StreamingResponse(
        stream(), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.delete("/{device_id}/locations")
async def delete_locations(
    device_id: int,
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    user=Depends(current_user),
):
    """期間指定で削除(F-09)。期間を省くとその端末の全履歴。本人か管理者だけ。"""
    pool = db.get_pool()
    await device_for(pool, user, device_id, write=True)
    start, end = aware(start), aware(end)
    check_range(start, end)
    status = await pool.execute(f"DELETE FROM locations WHERE device_id = $1 AND {_RANGE}", device_id, start, end)
    return {"deleted": int(status.split()[-1])}


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, user=Depends(current_user)):
    """端末ごと削除(履歴も消える)。次に位置が届くと作り直される。"""
    pool = db.get_pool()
    await device_for(pool, user, device_id, write=True)
    await pool.execute("DELETE FROM devices WHERE id = $1", device_id)


@router.get("/{device_id}/stats")
async def stats(device_id: int, days: int = Query(7, ge=1, le=93), user=Depends(current_user)):
    """日別の点数と移動距離(F-15)。精度が 100 m より悪い点は距離の計算から外す。"""
    pool = db.get_pool()
    await device_for(pool, user, device_id)
    rows = await pool.fetch(
        """
        SELECT (recorded_at AT TIME ZONE $3)::date AS day, count(*) AS points,
               CASE WHEN count(*) > 1
                    THEN ST_Length(ST_MakeLine(geom ORDER BY recorded_at)::geography) ELSE 0 END AS meters
        FROM locations
        WHERE device_id = $1 AND recorded_at >= now() - make_interval(days => $2)
              AND (accuracy_m IS NULL OR accuracy_m <= 100)
        GROUP BY 1 ORDER BY 1
        """,
        device_id,
        days,
        settings.tz.key,
    )
    return [{"day": r["day"].isoformat(), "points": r["points"], "meters": round(r["meters"])} for r in rows]
