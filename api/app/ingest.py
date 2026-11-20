"""位置を DB に保存する。MQTT・HTTP・Recorder 取り込みの共通処理。"""
import asyncpg

from .owntracks import Location


async def save_location(conn: asyncpg.Connection, user_name: str, device_name: str, loc: Location) -> dict | None:
    """保存できたら配信用のイベントを返す。重複・無効化された利用者なら None。

    利用者と端末は初めて届いたときに自動で作る(Web のパスワードは未設定)。
    """
    async with conn.transaction():
        # 毎回 UPDATE すると行が増えるので、まず SELECT し、無いときだけ作る
        user = await conn.fetchrow("SELECT id, disabled FROM users WHERE name = $1", user_name)
        if user is None:
            await conn.execute("INSERT INTO users (name) VALUES ($1) ON CONFLICT (name) DO NOTHING", user_name)
            user = await conn.fetchrow("SELECT id, disabled FROM users WHERE name = $1", user_name)
        if user["disabled"]:
            return None
        device = await conn.fetchrow("SELECT id, tid FROM devices WHERE user_id = $1 AND name = $2", user["id"], device_name)
        if device is None or (loc.tid and loc.tid != device["tid"]):
            device = await conn.fetchrow(
                """
                INSERT INTO devices (user_id, name, tid) VALUES ($1, $2, $3)
                ON CONFLICT (user_id, name) DO UPDATE SET tid = COALESCE(EXCLUDED.tid, devices.tid)
                RETURNING id, tid
                """,
                user["id"],
                device_name,
                loc.tid,
            )
        inserted = await conn.fetchval(
            """
            INSERT INTO locations (device_id, recorded_at, geom, accuracy_m, altitude_m,
                                   velocity_kmh, battery_pct, conn, raw)
            VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326), $5, $6, $7, $8, $9, $10)
            ON CONFLICT (device_id, recorded_at) DO NOTHING
            RETURNING id
            """,
            device["id"],
            loc.recorded_at,
            loc.lon,
            loc.lat,
            loc.accuracy_m,
            loc.altitude_m,
            loc.velocity_kmh,
            loc.battery_pct,
            loc.conn,
            loc.raw,
        )
        if inserted is None:
            return None
        await conn.execute(
            "UPDATE devices SET last_seen_at = GREATEST(last_seen_at, $2) WHERE id = $1",
            device["id"],
            loc.recorded_at,
        )
    return {
        "type": "location",
        "device_id": device["id"],
        "user_id": user["id"],
        "user_name": user_name,
        "device_name": device_name,
        "tid": device["tid"],
        "lat": loc.lat,
        "lon": loc.lon,
        "acc": loc.accuracy_m,
        "batt": loc.battery_pct,
        "vel": loc.velocity_kmh,
        "recorded_at": loc.recorded_at.isoformat(),
    }
