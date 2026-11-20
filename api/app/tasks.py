"""定期処理: 古い履歴の自動削除(F-12)と、受信途絶の通知(運用・監視)。"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import db, notify
from .config import Settings

log = logging.getLogger(__name__)


async def retention_loop(settings: Settings, interval_s: float = 6 * 3600) -> None:
    while True:
        try:
            await purge_once(settings.retention_days)
        except Exception:
            log.exception("定期削除に失敗しました")
        await asyncio.sleep(interval_s)


async def purge_once(retention_days: int) -> int:
    pool = db.get_pool()
    deleted = 0
    if retention_days > 0:
        status = await pool.execute(
            "DELETE FROM locations WHERE recorded_at < now() - make_interval(days => $1)", retention_days
        )
        deleted = int(status.split()[-1])
        await pool.execute(
            "DELETE FROM place_events WHERE occurred_at < now() - make_interval(days => $1)", retention_days
        )
        if deleted:
            log.info("保存期間(%d 日)を過ぎた位置を %d 件削除しました", retention_days, deleted)
    await pool.execute("DELETE FROM sessions WHERE expires_at < now()")
    return deleted


async def stale_alert_loop(settings: Settings, interval_s: float = 600) -> None:
    if not settings.alert_webhook_url or settings.alert_stale_hours <= 0:
        return
    alerted: set[int] = set()
    while True:
        try:
            limit = datetime.now(timezone.utc) - timedelta(hours=settings.alert_stale_hours)
            rows = await db.get_pool().fetch(
                """
                SELECT d.id, u.name AS user_name, d.name, d.last_seen_at
                FROM devices d JOIN users u ON u.id = d.user_id
                WHERE NOT u.disabled AND d.last_seen_at IS NOT NULL
                """
            )
            for r in rows:
                label = f"{r['user_name']}/{r['name']}"
                if r["last_seen_at"] < limit and r["id"] not in alerted:
                    since = r["last_seen_at"].astimezone(settings.tz).strftime("%m/%d %H:%M")
                    await notify.send(f"[map-tracking] {label} の位置が {since} から届いていません")
                    alerted.add(r["id"])
                elif r["last_seen_at"] >= limit and r["id"] in alerted:
                    await notify.send(f"[map-tracking] {label} の受信が再開しました")
                    alerted.discard(r["id"])
        except Exception:
            log.exception("受信途絶の確認に失敗しました")
        await asyncio.sleep(interval_s)
