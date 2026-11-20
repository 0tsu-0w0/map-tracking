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
    """owntracks/<user>/<device> を (user, device) にする。event や cmd などのサブトピックは対象外。"""
    parts = topic.split("/")
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
