"""OwnTracks のトピックとメッセージ(JSON)の解釈。DB には依存しない。"""
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# トピックの各要素。MQTT のワイルドカードや区切りを含まない、短い文字列だけ受け付ける
_NAME_RE = re.compile(r"^[^/+#\s]{1,64}$")
# 新しく作る利用者名(scripts/add-user.sh と同じ規則)
USER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Location:
    recorded_at: datetime
    lat: float
    lon: float
    accuracy_m: float | None
    altitude_m: float | None
    velocity_kmh: float | None
    battery_pct: int | None
    conn: str | None
    tid: str | None
    raw: dict[str, Any]


def valid_name(s: str | None) -> bool:
    return bool(s) and bool(_NAME_RE.match(s))  # type: ignore[arg-type]


def parse_topic(topic: str) -> tuple[str, str] | None:
    """owntracks/<user>/<device>(位置)と owntracks/<user>/<device>/event(出入り)を (user, device) にする。
    cmd・info・waypoints などほかのサブトピックは対象外。"""
    parts = topic.split("/")
    if len(parts) == 4 and parts[3] == "event":
        parts = parts[:3]
    if len(parts) != 3 or parts[0] != "owntracks":
        return None
    user, device = parts[1], parts[2]
    if not (valid_name(user) and valid_name(device)):
        return None
    return user, device


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def parse_location(payload: Any) -> Location | None:
    """_type が location で、緯度・経度・時刻がそろったものだけ Location にする。"""
    if not isinstance(payload, dict) or payload.get("_type") != "location":
        return None
    lat, lon, tst = _num(payload.get("lat")), _num(payload.get("lon")), _num(payload.get("tst"))
    if lat is None or lon is None or tst is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or tst <= 0:
        return None
    try:
        recorded_at = datetime.fromtimestamp(int(tst), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None

    batt = _num(payload.get("batt"))
    battery = int(batt) if batt is not None and 0 <= batt <= 100 else None
    acc = _num(payload.get("acc"))
    conn = payload.get("conn")
    tid = payload.get("tid")
    return Location(
        recorded_at=recorded_at,
        lat=lat,
        lon=lon,
        accuracy_m=acc if acc is not None and acc >= 0 else None,
        altitude_m=_num(payload.get("alt")),
        velocity_kmh=_num(payload.get("vel")),
        battery_pct=battery,
        conn=conn[:8] if isinstance(conn, str) else None,
        tid=tid[:8] if isinstance(tid, str) and tid else None,
        raw=payload,
    )


@dataclass(frozen=True)
class Transition:
    """OwnTracks の端末側ジオフェンス(Regions)の出入り。"""

    occurred_at: datetime
    event: str  # enter / leave
    place_name: str
    lat: float | None
    lon: float | None


def parse_transition(payload: Any) -> Transition | None:
    if not isinstance(payload, dict) or payload.get("_type") != "transition":
        return None
    event, desc, tst = payload.get("event"), payload.get("desc"), _num(payload.get("tst"))
    if event not in ("enter", "leave") or tst is None or tst <= 0:
        return None
    name = desc.strip()[:100] if isinstance(desc, str) and desc.strip() else "(名前なし)"
    try:
        occurred_at = datetime.fromtimestamp(int(tst), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    lat, lon = _num(payload.get("lat")), _num(payload.get("lon"))
    return Transition(occurred_at=occurred_at, event=event, place_name=name, lat=lat, lon=lon)


def decrypt_payload(payload: Any, secret: str) -> Any | None:
    """OwnTracks の暗号化ペイロード({"_type":"encrypted","data":...})を復号する。

    鍵は利用者が端末に入れた文字列(UTF-8)を 32 バイトに切り詰め・0 埋めしたもの。
    data は base64(nonce 24 バイト + libsodium secretbox の暗号文)。失敗したら None。
    """
    import base64
    import json

    from nacl.exceptions import CryptoError
    from nacl.secret import SecretBox

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), str):
        return None
    key = secret.encode("utf-8")[:32].ljust(32, bytes(1))
    try:
        plain = SecretBox(key).decrypt(base64.b64decode(payload["data"], validate=True))
        return json.loads(plain)
    except (CryptoError, ValueError, UnicodeDecodeError, RecursionError):
        return None
