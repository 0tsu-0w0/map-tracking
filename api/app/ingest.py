"""届いたメッセージを保存する。MQTT・HTTP・Recorder 取り込みの共通処理。"""
import logging
from typing import Any

import asyncpg

from . import notify
from .config import settings
from .hub import Hub
from .owntracks import Location, Transition, decrypt_payload, parse_location, parse_transition

log = logging.getLogger(__name__)

# 場所から出たと判定するまでの余裕(境界付近で出入りを繰り返さないため)
LEAVE_MARGIN_M = 30
# これより精度の悪い点では出入りを判定しない
MAX_ACCURACY_FOR_PLACES_M = 500


async def _user_and_device(conn: asyncpg.Connection, user_name: str, device_name: str, tid: str | None):
    """利用者と端末を返す。初めて届いたときに自動で作る(Web のパスワードは未設定)。"""
    # 毎回 UPDATE すると行が増えるので、まず SELECT し、無いときだけ作る
    user = await conn.fetchrow("SELECT id, disabled FROM users WHERE name = $1", user_name)
    if user is None:
        await conn.execute("INSERT INTO users (name) VALUES ($1) ON CONFLICT (name) DO NOTHING", user_name)
        user = await conn.fetchrow("SELECT id, disabled FROM users WHERE name = $1", user_name)
    if user["disabled"]:
        return user, None
    device = await conn.fetchrow(
        "SELECT id, tid, last_seen_at FROM devices WHERE user_id = $1 AND name = $2", user["id"], device_name
    )
    if device is None or (tid and tid != device["tid"]):
        device = await conn.fetchrow(
            """
            INSERT INTO devices (user_id, name, tid) VALUES ($1, $2, $3)
            ON CONFLICT (user_id, name) DO UPDATE SET tid = COALESCE(EXCLUDED.tid, devices.tid)
            RETURNING id, tid, last_seen_at
            """,
            user["id"],
            device_name,
            tid,
        )
    return user, device


async def save_location(conn: asyncpg.Connection, user_name: str, device_name: str, loc: Location) -> dict | None:
    """保存できたら配信用のイベントを返す。重複・無効化された利用者なら None。

    イベントの "newest" は、その端末で一番新しい点かどうか(出入りの判定はこれが True のときだけ行う)。
    """
    async with conn.transaction():
        user, device = await _user_and_device(conn, user_name, device_name, loc.tid)
        if device is None:
            return None
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
    last = device["last_seen_at"]
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
        "newest": last is None or loc.recorded_at >= last,
    }


def _place_event(row: dict, ev: dict, kind: str, source: str, occurred_at: str) -> dict:
    return {
        "type": "place",
        "event": kind,
        "source": source,
        "place_name": row["place_name"],
        "place_id": row.get("place_id"),
        "device_id": ev["device_id"],
        "user_id": ev["user_id"],
        "user_name": ev["user_name"],
        "device_name": ev["device_name"],
        "occurred_at": occurred_at,
    }


async def evaluate_places(conn: asyncpg.Connection, ev: dict) -> list[tuple[dict, int | None, bool]]:
    """最新の位置で、各場所への出入りを判定する(F-14、サーバー側ジオフェンス)。

    対象は、端末の位置を見られる人(本人・共有を受けた人・管理者)が登録した場所。
    初めて判定するときは今の状態を覚えるだけで、出入りとしては記録しない。
    戻り値は (配信用イベント, 場所の持ち主, 通知するか) の一覧。
    """
    if not ev.get("newest") or (ev["acc"] is not None and ev["acc"] > MAX_ACCURACY_FOR_PLACES_M):
        return []
    rows = await conn.fetch(
        """
        SELECT p.id, p.name, p.user_id, p.notify, p.radius_m, pp.inside,
               ST_Distance(p.geom::geography, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography) AS dist
        FROM places p
        JOIN users o ON o.id = p.user_id AND NOT o.disabled
        LEFT JOIN place_presence pp ON pp.place_id = p.id AND pp.device_id = $1
        WHERE p.user_id = $4 OR o.role = 'admin'
           OR EXISTS (SELECT 1 FROM shares s WHERE s.owner_user_id = $4 AND s.viewer_user_id = p.user_id)
        """,
        ev["device_id"],
        ev["lon"],
        ev["lat"],
        ev["user_id"],
    )
    out = []
    for r in rows:
        margin = max(LEAVE_MARGIN_M, min(ev["acc"] or 0, 100))
        inside = r["dist"] <= r["radius_m"] if not r["inside"] else r["dist"] <= r["radius_m"] + margin
        if r["inside"] is not None and inside == r["inside"]:
            continue
        await conn.execute(
            """
            INSERT INTO place_presence (place_id, device_id, inside, changed_at) VALUES ($1, $2, $3, $4)
            ON CONFLICT (place_id, device_id) DO UPDATE SET inside = EXCLUDED.inside, changed_at = EXCLUDED.changed_at
            """,
            r["id"],
            ev["device_id"],
            inside,
            ev["recorded_at_dt"],
        )
        if r["inside"] is None:
            continue  # 初回は状態を覚えるだけ
        kind = "enter" if inside else "leave"
        await conn.execute(
            """
            INSERT INTO place_events (place_id, place_owner, device_id, place_name, event, source, occurred_at)
            VALUES ($1, $2, $3, $4, $5, 'server', $6) ON CONFLICT DO NOTHING
            """,
            r["id"],
            r["user_id"],
            ev["device_id"],
            r["name"],
            kind,
            ev["recorded_at_dt"],
        )
        pe = _place_event({"place_name": r["name"], "place_id": r["id"]}, ev, kind, "server", ev["recorded_at"])
        out.append((pe, r["user_id"], r["notify"]))
    return out


async def save_transition(conn: asyncpg.Connection, user_name: str, device_name: str, tr: Transition) -> dict | None:
    """OwnTracks 側のジオフェンス(Regions)の出入りを記録する。"""
    async with conn.transaction():
        user, device = await _user_and_device(conn, user_name, device_name, None)
        if device is None:
            return None
        inserted = await conn.fetchval(
            """
            INSERT INTO place_events (device_id, place_name, event, source, occurred_at)
            VALUES ($1, $2, $3, 'device', $4) ON CONFLICT DO NOTHING RETURNING id
            """,
            device["id"],
            tr.place_name,
            tr.event,
            tr.occurred_at,
        )
    if inserted is None:
        return None
    base = {"device_id": device["id"], "user_id": user["id"], "user_name": user_name, "device_name": device_name}
    return _place_event({"place_name": tr.place_name}, base, tr.event, "device", tr.occurred_at.isoformat())


def _notice(pe: dict) -> str:
    verb = "に着きました" if pe["event"] == "enter" else "を出ました"
    return f"[map-tracking] {pe['user_name']}/{pe['device_name']} が {pe['place_name']} {verb}"


async def ingest_payload(pool: asyncpg.Pool, hub: Hub | None, user_name: str, device_name: str, payload: Any) -> list[dict]:
    """1 メッセージを処理し、配信したイベントを返す。"""
    if isinstance(payload, dict) and payload.get("_type") == "encrypted":
        key = await pool.fetchval("SELECT enc_key FROM users WHERE name = $1 AND NOT disabled", user_name)
        decrypted = decrypt_payload(payload, key) if key else None
        if decrypted is None:
            log.warning("暗号化ペイロードを復号できませんでした(%s/%s): 鍵が未設定か違います", user_name, device_name)
            return []
        payload = decrypted

    events: list[dict] = []
    loc = parse_location(payload)
    if loc is not None:
        async with pool.acquire() as conn:
            ev = await save_location(conn, user_name, device_name, loc)
            if ev is None:
                return []
            ev["recorded_at_dt"] = loc.recorded_at
            async with conn.transaction():
                places = await evaluate_places(conn, ev)
            del ev["recorded_at_dt"]
        events.append(ev)
        if hub:
            await hub.broadcast(pool, ev)
        for pe, owner, should_notify in places:
            events.append(pe)
            if hub:
                # サーバー側の場所の出入りは、場所の持ち主・端末の持ち主・管理者にだけ配る
                admins = {r["id"] for r in await pool.fetch("SELECT id FROM users WHERE role = 'admin'")}
                await hub.broadcast(pool, pe, only={owner, pe["user_id"]} | admins)
            if should_notify:
                notify.send_later(_notice(pe))
        return events

    tr = parse_transition(payload)
    if tr is not None:
        async with pool.acquire() as conn:
            pe = await save_transition(conn, user_name, device_name, tr)
        if pe:
            events.append(pe)
            if hub:
                await hub.broadcast(pool, pe)
            if settings.notify_device_transitions:
                notify.send_later(_notice(pe))
    return events
