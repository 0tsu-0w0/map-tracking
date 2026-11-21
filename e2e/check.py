"""構成一式(caddy・mosquitto・api・db)の通しテスト。CI の e2e ジョブから呼ぶ。

スマホ(OwnTracks)と同じ経路 = WSS:443 → Caddy → Mosquitto → API → DB で位置を送り、
地図画面の API と WebSocket で受け取れるかを確かめる。

前提: scripts/setup.sh --local で起動済み、scripts/member.sh add taro / add jiro --encrypt 済み、
     /etc/hosts で mqtt.localhost・map.localhost が 127.0.0.1、e2e/root.crt に Caddy のローカル CA。
"""
import asyncio
import base64
import json
import os
import ssl
import sys
import time
from pathlib import Path

import aiomqtt
import httpx
from nacl.secret import SecretBox
from nacl.utils import random as nacl_random
from websockets.asyncio.client import connect as ws_connect

ROOT = Path(__file__).resolve().parent.parent
MQTT_HOST = "mqtt.localhost"
APP = "https://map.localhost"
CA = str(ROOT / "e2e" / "root.crt")
ADMIN_PASS = os.environ["SETUP_ADMIN_PASS"]

ssl_ctx = ssl.create_default_context(cafile=CA)
failures = 0


def check(cond: bool, label: str) -> None:
    global failures
    print(("OK   " if cond else "NG   ") + label, flush=True)
    if not cond:
        failures += 1


def member(name: str, device: str = "phone") -> dict:
    return json.loads((ROOT / "members" / name / f"{device}.otrc").read_text(encoding="utf-8"))


def web_password(name: str) -> str:
    for line in (ROOT / "members" / name / "web-login.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("パスワード: "):
            return line.split(": ", 1)[1]
    raise RuntimeError("web-login.txt にパスワードがありません")


def mqtt_client(username: str | None, password: str | None, ident: str) -> aiomqtt.Client:
    return aiomqtt.Client(
        hostname=MQTT_HOST,
        port=443,
        transport="websockets",
        websocket_path="/",  # OwnTracks はパスを指定できないので / で接続する
        tls_context=ssl_ctx,
        username=username,
        password=password,
        identifier=ident,
        timeout=10,
    )


def loc(lat: float, lon: float, tst: int | None = None, tid: str = "ta") -> dict:
    return {"_type": "location", "tid": tid, "lat": lat, "lon": lon, "acc": 8, "batt": 77, "tst": tst or int(time.time())}


async def publish(user: dict, topic: str, payload: dict) -> None:
    async with mqtt_client(user["username"], user["password"], user["clientId"] + "-e2e") as c:
        await c.publish(topic, json.dumps(payload), qos=1)


async def mqtt_auth_rejected(username: str | None, password: str | None) -> bool:
    try:
        async with mqtt_client(username, password, "e2e-reject"):
            return False
    except aiomqtt.MqttError:
        return True


async def wait_for(ws, pred, timeout: float = 15) -> dict | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=end - time.monotonic()))
        except TimeoutError:
            return None
        if pred(msg):
            return msg
    return None


async def main() -> None:
    taro, jiro = member("taro"), member("jiro")
    http = httpx.AsyncClient(base_url=APP, verify=ssl_ctx, headers={"X-Requested-With": "fetch"}, timeout=15)

    # --- 外向きの設定 ---
    r = await http.get("/")
    check(r.status_code == 200 and "<div id=\"root\">" in r.text, "地図画面が HTTPS で開ける")
    csp = r.headers.get("content-security-policy", "")
    check("script-src 'self'" in csp and "strict-transport-security" in r.headers, "CSP・HSTS が付いている")
    check((await http.get("/healthz")).status_code == 404, "/healthz は外から見えない")

    # --- MQTT の認証 ---
    check(await mqtt_auth_rejected(None, None), "MQTT: 匿名接続は拒否される")
    check(await mqtt_auth_rejected("taro", "wrong-password"), "MQTT: パスワード違いは拒否される")

    # --- Web ログイン ---
    r = await http.post("/api/auth/login", json={"name": "admin", "password": ADMIN_PASS})
    check(r.status_code == 200, "管理者でログインできる")
    cookie = f"ot_session={r.cookies['ot_session']}"
    http.headers["Cookie"] = cookie
    http.cookies.clear()

    async with ws_connect(
        "wss://map.localhost/api/ws", ssl=ssl_ctx, additional_headers={"Cookie": cookie}, origin="https://map.localhost"
    ) as ws:
        # --- スマホと同じ経路で位置を送る ---
        await publish(taro, "owntracks/taro/phone", loc(35.6812, 139.7671))
        ev = await wait_for(ws, lambda m: m.get("type") == "location" and m.get("user_name") == "taro")
        check(ev is not None and abs(ev["lat"] - 35.6812) < 1e-6, "MQTT over WSS で送った位置が WebSocket で届く")
        devs = {(d["user_name"], d["name"]): d for d in (await http.get("/api/devices")).json()}
        check(("taro", "phone") in devs and devs[("taro", "phone")]["last"]["batt"] == 77, "位置が DB に保存され、一覧に出る")

        # --- ACL: 他人のトピックには書けない ---
        await publish(taro, "owntracks/jiro/phone", loc(1.0, 1.0))
        await asyncio.sleep(3)
        users = {u["name"]: u for u in (await http.get("/api/admin/users")).json()}
        check(users.get("jiro", {}).get("devices", 0) == 0, "ACL: taro は jiro のトピックに書けない")

        # --- 暗号化ペイロード ---
        key = jiro["encryptionKey"].encode()[:32].ljust(32, bytes(1))
        data = SecretBox(key).encrypt(json.dumps(loc(34.7025, 135.4959, tid="ji")).encode(), nacl_random(24))
        await publish(jiro, "owntracks/jiro/phone", {"_type": "encrypted", "data": base64.b64encode(bytes(data)).decode()})
        ev = await wait_for(ws, lambda m: m.get("type") == "location" and m.get("user_name") == "jiro")
        check(ev is not None and abs(ev["lat"] - 34.7025) < 1e-6, "暗号化された位置を復号して保存できる")

        # --- HTTP モード ---
        r = await httpx.AsyncClient(verify=ssl_ctx).post(
            f"{APP}/api/pub",
            json=loc(35.0, 135.0),
            auth=("taro", web_password("taro")),
            headers={"X-Limit-U": "taro", "X-Limit-D": "http"},
        )
        check(r.status_code == 200 and r.json() == [], "HTTP モード(/api/pub)で送れる")

        # --- ジオフェンス ---
        r = await http.post("/api/places", json={"name": "東京駅", "lat": 35.6812, "lon": 139.7671, "radius_m": 200})
        check(r.status_code == 201, "場所を登録できる")
        t = int(time.time()) + 60
        await publish(taro, "owntracks/taro/phone", loc(35.70, 139.70, t))  # 遠く(初回は状態を覚えるだけ)
        await wait_for(ws, lambda m: m.get("type") == "location" and m.get("user_name") == "taro")
        await publish(taro, "owntracks/taro/phone", loc(35.6813, 139.7672, t + 60))
        ev = await wait_for(ws, lambda m: m.get("type") == "place")
        check(ev is not None and ev["event"] == "enter" and ev["place_name"] == "東京駅", "場所に入ると「着」が届く")

        # --- OwnTracks 側の Regions ---
        await publish(taro, "owntracks/taro/phone/event", {"_type": "transition", "event": "leave", "desc": "会社", "tst": t + 90})
        ev = await wait_for(ws, lambda m: m.get("type") == "place" and m.get("source") == "device")
        check(ev is not None and ev["place_name"] == "会社", "端末側の出入り(transition)も届く")

    # --- 書き出し ---
    dev_id = devs[("taro", "phone")]["id"]
    r = await http.get(f"/api/devices/{dev_id}/export?format=gpx")
    check(r.status_code == 200 and r.text.count("<trkpt") >= 3, "GPX で書き出せる")

    await http.aclose()
    if failures:
        print(f"\n{failures} 件失敗しました")
        sys.exit(1)
    print("\nすべて成功しました")


asyncio.run(main())
