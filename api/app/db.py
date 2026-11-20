"""PostgreSQL への接続プールとスキーマ作成。"""
import asyncio
import json
import logging
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)
SCHEMA = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")

pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    # jsonb を dict のまま読み書きする
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def connect(dsn: str | None, attempts: int = 30) -> asyncpg.Pool:
    """DB が起動するまで待ってから接続し、スキーマを作る。"""
    global pool
    for i in range(attempts):
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, init=_init_conn)
            break
        except (OSError, asyncpg.CannotConnectNowError, asyncpg.InvalidCatalogNameError) as e:
            if i == attempts - 1:
                raise
            log.info("DB 接続待ち (%s)", e)
            await asyncio.sleep(2)
    assert pool is not None
    async with pool.acquire() as conn:
        # 複数プロセスが同時に起動してもスキーマ作成がぶつからないようにする
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(7428150)")
            await conn.execute(SCHEMA)
    return pool


async def close() -> None:
    global pool
    if pool is not None:
        await pool.close()
        pool = None


def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("DB に接続していません")
    return pool
