"""알림 저장/조회 (asyncpg). db_manager.pool 을 그대로 사용한다."""
from __future__ import annotations

import logging

from models.rul_repository import _parse_ts

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS alerts (
    id              BIGSERIAL PRIMARY KEY,
    time            TIMESTAMPTZ NOT NULL,
    asset_id        VARCHAR(50) NOT NULL,
    level           VARCHAR(10) NOT NULL,
    cycle           INT,
    health_index    DOUBLE PRECISION,
    rul_pred        DOUBLE PRECISION,
    message         TEXT,
    acknowledged    BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_at TIMESTAMPTZ
);
"""
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts (time DESC);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_asset ON alerts (asset_id, time DESC);",
]
INSERT_SQL = """
INSERT INTO alerts (time, asset_id, level, cycle, health_index, rul_pred, message)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""
COLUMNS = "id, time, asset_id, level, cycle, health_index, rul_pred, message, acknowledged, acknowledged_at"


async def init_alert_table(pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE)
        for q in CREATE_INDEXES:
            await conn.execute(q)
    logger.info("alerts 테이블이 준비되었습니다.")


async def insert_alerts(pool, alerts: list[dict]) -> int:
    if not alerts:
        return 0
    records = [(_parse_ts(a.get("time")), a["asset_id"], a["level"], a.get("cycle"),
                a.get("health_index"), a.get("rul_pred"), a.get("message")) for a in alerts]
    async with pool.acquire() as conn:
        await conn.executemany(INSERT_SQL, records)
    return len(records)


def build_list_query(limit: int, unacked_only: bool, level: str | None):
    conds, args = [], []
    if unacked_only:
        conds.append("NOT acknowledged")
    if level:
        args.append(level)
        conds.append(f"level = ${len(args)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    args.append(limit)
    sql = f"SELECT {COLUMNS} FROM alerts {where} ORDER BY time DESC, id DESC LIMIT ${len(args)}"
    return sql, args


async def fetch_alerts(pool, limit: int = 50, unacked_only: bool = False, level: str | None = None):
    sql, args = build_list_query(limit, unacked_only, level)
    async with pool.acquire() as conn:
        return await conn.fetch(sql, *args)


async def alert_summary(pool) -> dict:
    sql = "SELECT level, count(*) AS total, count(*) FILTER (WHERE NOT acknowledged) AS unacked FROM alerts GROUP BY level"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    out = {"critical": {"total": 0, "unacked": 0}, "warning": {"total": 0, "unacked": 0}}
    for r in rows:
        out[r["level"]] = {"total": int(r["total"]), "unacked": int(r["unacked"])}
    return out


async def acknowledge(pool, alert_id: int) -> bool:
    sql = "UPDATE alerts SET acknowledged = TRUE, acknowledged_at = now() WHERE id = $1 AND NOT acknowledged RETURNING id"
    async with pool.acquire() as conn:
        return (await conn.fetchval(sql, alert_id)) is not None


async def acknowledge_all(pool) -> int:
    sql = "UPDATE alerts SET acknowledged = TRUE, acknowledged_at = now() WHERE NOT acknowledged"
    async with pool.acquire() as conn:
        status = await conn.execute(sql)            # 예: 'UPDATE 12'
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0
