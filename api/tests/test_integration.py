"""PostgreSQL + PostGIS を使う結合テスト。DATABASE_URL があるときだけ動く(CI で実行)。"""
import asyncio
import base64
import json
import os
import time

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL がない")

FETCH = {"X-Requested-With": "fetch"}


def _reset_db():
    import asyncpg

    async def run():
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            for t in ("place_events", "place_presence", "places", "sessions", "shares", "locations", "devices", "users"):
                await conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    _reset_db()
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, name, password):
    r = client.post("/api/auth/login", json={"name": name, "password": password}, headers=FETCH)
    assert r.status_code == 200, r.text
    token = r.cookies["ot_session"]
    client.cookies.clear()
    return {**FETCH, "Cookie": f"ot_session={token}"}


def basic(name, password, device="phone"):
    tok = base64.b64encode(f"{name}:{password}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "X-Limit-U": name, "X-Limit-D": device}


def loc(tst, lat=35.6812, lon=139.7671, **kw):
    return {"_type": "location", "tid": "tr", "lat": lat, "lon": lon, "acc": 10, "batt": 90, "tst": tst, **kw}


NOW = int(time.time())
state: dict = {}


def test_login_and_csrf(client):
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"name": "admin", "password": "nope"}, headers=FETCH)
    assert r.status_code == 401
    # X-Requested-With が無い書き換えは拒否
    assert client.post("/api/auth/login", json={"name": "admin", "password": "admin-password"}).status_code == 403
    state["admin"] = h = login(client, "admin", "admin-password")
    assert client.get("/api/auth/me", headers=h).json()["role"] == "admin"


def test_admin_creates_users(client):
    h = state["admin"]
    for name in ("taro", "hanako"):
        r = client.post("/api/admin/users", json={"name": name, "password": f"{name}-password"}, headers=h)
        assert r.status_code == 201, r.text
    assert client.post("/api/admin/users", json={"name": "taro", "password": "x" * 8}, headers=h).status_code == 409
    assert client.post("/api/admin/users", json={"name": "bad/name", "password": "x" * 8}, headers=h).status_code == 400
    state["taro"] = login(client, "taro", "taro-password")
    state["hanako"] = login(client, "hanako", "hanako-password")
    # 一般ユーザーは管理 API を使えない
    assert client.get("/api/admin/users", headers=state["taro"]).status_code == 403


def test_http_pub_and_dedupe(client):
    assert client.post("/api/pub", json=loc(NOW - 120), headers=basic("taro", "wrong")).status_code == 401
    for _ in range(2):  # 同じ点を 2 回送っても 1 件だけ
        r = client.post("/api/pub", json=loc(NOW - 120), headers=basic("taro", "taro-password"))
        assert r.status_code == 200 and r.json() == []
    client.post("/api/pub", json=loc(NOW - 60, lat=35.6900, lon=139.7000), headers=basic("taro", "taro-password"))
    devs = client.get("/api/devices", headers=state["taro"]).json()
    assert len(devs) == 1
    d = devs[0]
    assert (d["user_name"], d["name"], d["tid"], d["can_edit"]) == ("taro", "phone", "tr", True)
    assert d["last"]["lat"] == pytest.approx(35.69)
    state["dev"] = d["id"]
    r = client.get(f"/api/devices/{d['id']}/locations", headers=state["taro"]).json()
    assert r["count"] == 2 and r["features"][0]["geometry"]["type"] == "LineString"


def test_sharing_controls_visibility(client):
    dev, hanako, taro = state["dev"], state["hanako"], state["taro"]
    assert client.get("/api/devices", headers=hanako).json() == []
    assert client.get(f"/api/devices/{dev}/locations", headers=hanako).status_code == 404
    assert client.post("/api/shares", json={"viewer": "hanako"}, headers=taro).status_code == 201
    assert client.post("/api/shares", json={"viewer": "taro"}, headers=taro).status_code == 400
    assert [d["id"] for d in client.get("/api/devices", headers=hanako).json()] == [dev]
    assert client.get(f"/api/devices/{dev}/locations", headers=hanako).status_code == 200
    # 見るだけで、消せない
    assert client.delete(f"/api/devices/{dev}/locations", headers=hanako).status_code == 403
    s = client.get("/api/shares", headers=hanako).json()
    assert s["shared_with_me"][0]["name"] == "taro"


def test_websocket_push(client):
    headers = {"Cookie": state["hanako"]["Cookie"]}
    with client.websocket_connect("/api/ws", headers=headers) as ws:
        client.post("/api/pub", json=loc(NOW - 30, lat=35.7), headers=basic("taro", "taro-password"))
        ev = ws.receive_json()
        assert ev["type"] == "location" and ev["user_name"] == "taro" and ev["lat"] == 35.7


def test_websocket_rejects_other_origin(client):
    from starlette.websockets import WebSocketDisconnect

    headers = {"Cookie": state["hanako"]["Cookie"], "Origin": "https://evil.example"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", headers=headers) as ws:
            ws.receive_text()


def test_mqtt_message_creates_user_and_device(client):
    from app import mqtt

    payload = json.dumps(loc(NOW - 10, tid="jr")).encode()
    evs = client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/jiro/android", payload)
    assert len(evs) == 1 and evs[0]["user_name"] == "jiro"
    again = client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/jiro/android", payload)
    assert again == []  # 重複
    users = {u["name"]: u for u in client.get("/api/admin/users", headers=state["admin"]).json()}
    assert users["jiro"]["has_password"] is False and users["jiro"]["devices"] == 1


def test_export_formats(client):
    dev, h = state["dev"], state["taro"]
    gpx = client.get(f"/api/devices/{dev}/export?format=gpx", headers=h)
    assert gpx.status_code == 200 and gpx.text.count("<trkpt") == 3
    assert "attachment" in gpx.headers["content-disposition"]
    gj = client.get(f"/api/devices/{dev}/export?format=geojson", headers=h).json()
    assert len(gj["features"]) == 3
    csv_text = client.get(f"/api/devices/{dev}/export?format=csv", headers=h).text
    assert len(csv_text.strip().splitlines()) == 4


def test_stats(client):
    rows = client.get(f"/api/devices/{state['dev']}/stats?days=2", headers=state["taro"]).json()
    assert sum(r["points"] for r in rows) == 3
    assert sum(r["meters"] for r in rows) > 1000  # 東京駅付近から約 6 km 動いている


def test_delete_range(client):
    dev, h = state["dev"], state["taro"]
    from datetime import datetime, timezone

    cut = datetime.fromtimestamp(NOW - 90, tz=timezone.utc).isoformat()
    r = client.delete(f"/api/devices/{dev}/locations", params={"to": cut}, headers=h)
    assert r.json() == {"deleted": 1}
    assert client.get(f"/api/devices/{dev}/locations", headers=h).json()["count"] == 2


def test_retention_purge(client):
    from app import tasks

    client.post("/api/pub", json=loc(NOW - 400 * 86400), headers=basic("taro", "taro-password"))
    deleted = client.portal.call(tasks.purge_once, 365)
    assert deleted == 1


def test_disable_user_ends_session(client):
    users = {u["name"]: u for u in client.get("/api/admin/users", headers=state["admin"]).json()}
    r = client.patch(f"/api/admin/users/{users['hanako']['id']}", json={"disabled": True}, headers=state["admin"])
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=state["hanako"]).status_code == 401
    # 管理者は自分の権限を外せない
    r = client.patch(f"/api/admin/users/{users['admin']['id']}", json={"role": "member"}, headers=state["admin"])
    assert r.status_code == 400


def test_password_change(client):
    h = state["taro"]
    r = client.post("/api/auth/password", json={"current": "taro-password", "new": "taro-password-2"}, headers=h)
    assert r.status_code == 204
    assert client.get("/api/auth/me", headers=h).status_code == 200  # 今のセッションは残る
    login(client, "taro", "taro-password-2")


def test_healthz(client):
    assert client.get("/healthz").json()["db"] == "ok"


def test_export_filename_with_japanese_device(client):
    from app import mqtt

    payload = json.dumps(loc(NOW - 5)).encode()
    client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/taro/スマホ", payload)
    devs = {d["name"]: d for d in client.get("/api/devices", headers=state["taro"]).json()}
    r = client.get(f"/api/devices/{devs['スマホ']['id']}/export?format=csv", headers=state["taro"])
    assert r.status_code == 200
    assert "filename*=UTF-8''taro-%E3%82%B9" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"


def test_encrypted_payload(client):
    from test_units import encrypt_like_owntracks

    from app import mqtt

    h = state["taro"]
    assert client.put("/api/auth/encryption-key", json={"key": "family secret"}, headers=h).status_code == 204
    assert client.get("/api/auth/me", headers=h).json()["has_enc_key"] is True
    enc = json.dumps(encrypt_like_owntracks(loc(NOW - 3, lat=35.1), "family secret")).encode()
    evs = client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/taro/phone", enc)
    assert len(evs) == 1 and evs[0]["lat"] == 35.1
    bad = json.dumps(encrypt_like_owntracks(loc(NOW - 2), "wrong key")).encode()
    assert client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/taro/phone", bad) == []
    assert client.put("/api/auth/encryption-key", json={"key": None}, headers=h).status_code == 204


def test_places_enter_and_leave(client):
    from app import mqtt

    h, admin = state["taro"], state["admin"]
    # 東京駅のまわり 200 m を登録
    r = client.post("/api/places", json={"name": "東京駅", "lat": 35.6812, "lon": 139.7671, "radius_m": 200}, headers=h)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert [p["name"] for p in client.get("/api/places", headers=h).json()] == ["東京駅"]

    def send(t, lat, lon):
        payload = json.dumps(loc(t, lat=lat, lon=lon, acc=10)).encode()
        return client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/taro/phone", payload)

    base = NOW + 100
    first = send(base, 35.70, 139.70)  # 遠く(初回は状態を覚えるだけ)
    assert [e["type"] for e in first] == ["location"]
    entered = send(base + 60, 35.6815, 139.7670)
    assert [(e["type"], e.get("event")) for e in entered] == [("location", None), ("place", "enter")]
    assert send(base + 120, 35.6820, 139.7672)[1:] == []  # 中にいる間は何も起きない
    # 境界のすぐ外(半径 + 余裕の内側)ではまだ出ていない
    assert send(base + 180, 35.6812 + 0.0020, 139.7671)[1:] == []
    left = send(base + 240, 35.69, 139.78)
    assert left[1]["event"] == "leave"
    # 過去の点(後から届いた古い位置)では判定しない
    assert send(base - 30, 35.6812, 139.7671)[1:] == []

    dev = left[0]["device_id"]
    evs = client.get(f"/api/devices/{dev}/events", headers=h).json()
    assert [e["event"] for e in evs] == ["leave", "enter"]
    assert client.get(f"/api/devices/{dev}/events", headers=admin).status_code == 200
    # 場所を消しても記録は残る
    assert client.delete(f"/api/places/{pid}", headers=h).status_code == 204
    assert len(client.get(f"/api/devices/{dev}/events", headers=h).json()) == 2
    # 他人の場所は消せない
    assert client.delete(f"/api/places/{pid}", headers=admin).status_code == 404


def test_device_transition(client):
    from app import mqtt

    payload = json.dumps({"_type": "transition", "event": "enter", "desc": "会社", "tst": NOW + 500, "lat": 35, "lon": 139}).encode()
    evs = client.portal.call(mqtt.handle_message, client.app.state.hub, client.app.state.mqtt, "owntracks/taro/phone/event", payload)
    assert len(evs) == 1 and evs[0]["source"] == "device" and evs[0]["place_name"] == "会社"
    dev = evs[0]["device_id"]
    names = [e["place_name"] for e in client.get(f"/api/devices/{dev}/events", headers=state["taro"]).json()]
    assert "会社" in names


def test_login_limit_per_user(client):
    from app.auth import limiter

    try:
        for _ in range(10):
            client.post("/api/auth/login", json={"name": "jiro", "password": "wrong"}, headers=FETCH)
        r = client.post("/api/auth/login", json={"name": "jiro", "password": "wrong"}, headers=FETCH)
        assert r.status_code == 429
    finally:
        limiter._fails.clear()
