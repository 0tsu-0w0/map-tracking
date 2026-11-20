"""環境変数から設定を読む。値はすべて compose.b.yaml から渡す。"""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    v = os.environ.get(name, "")
    return int(v) if v.strip() else default


def _float(name: str, default: float) -> float:
    v = os.environ.get(name, "")
    return float(v) if v.strip() else default


@dataclass(frozen=True)
class Settings:
    # 空なら asyncpg が PGHOST / PGUSER / PGPASSWORD / PGDATABASE を読む
    database_url: str | None
    mqtt_host: str  # 空なら MQTT 購読をしない(テスト・HTTP 受信のみの運用)
    mqtt_port: int
    mqtt_user: str
    mqtt_pass: str
    mqtt_topic: str
    retention_days: int  # 0 なら自動削除しない
    tz: ZoneInfo
    cookie_secure: bool
    session_days: int
    map_style_url: str
    max_points: int
    alert_webhook_url: str
    alert_stale_hours: float  # 0 なら通知しない
    static_dir: str
    initial_admin_user: str
    initial_admin_pass: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("DATABASE_URL") or None,
            mqtt_host=os.environ.get("MQTT_HOST", ""),
            mqtt_port=_int("MQTT_PORT", 1883),
            mqtt_user=os.environ.get("MQTT_USER", "api"),
            mqtt_pass=os.environ.get("MQTT_PASS", ""),
            mqtt_topic=os.environ.get("MQTT_TOPIC", "owntracks/+/+"),
            retention_days=_int("RETENTION_DAYS", 365),
            tz=ZoneInfo(os.environ.get("TZ") or "Asia/Tokyo"),
            cookie_secure=_bool("COOKIE_SECURE", True),
            session_days=_int("SESSION_DAYS", 30),
            map_style_url=os.environ.get("MAP_STYLE_URL", ""),
            max_points=_int("MAX_POINTS", 20000),
            alert_webhook_url=os.environ.get("ALERT_WEBHOOK_URL", ""),
            alert_stale_hours=_float("ALERT_STALE_HOURS", 0),
            static_dir=os.environ.get("STATIC_DIR", "/app/static"),
            initial_admin_user=os.environ.get("INITIAL_ADMIN_USER", ""),
            initial_admin_pass=os.environ.get("INITIAL_ADMIN_PASS", ""),
        )


settings = Settings.from_env()
