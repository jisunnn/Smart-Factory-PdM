"""작업 지시서 저장·조회 (asyncpg). db_manager.pool 을 그대로 사용한다."""
from __future__ import annotations

import json
import logging

from models.rul_repository import _parse_ts

logger = logging.getLogger(__name__)

CREATE = """
CREATE TABLE IF NOT EXISTS work_orders (
    id              BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    alert_time      TIMESTAMPTZ,
    asset_id        VARCHAR(50) NOT NULL,
    source          VARCHAR(50),
    level           VARCHAR(10) NOT NULL,
    cycle           INT,
    sop_code        VARCHAR(20),
    llm_used        BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_reason TEXT,
    status          VARCHAR(10) NOT NULL DEFAULT 'open',
    text            TEXT,
    payload         JSONB,
    UNIQUE (asset_id, level, cycle, source)
);
"""
INSERT = """
INSERT INTO work_orders (alert_time, asset_id, source, level, cycle, sop_code, llm_used, fallback_reason, text, payload)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
ON CONFLICT (asset_id, level, cycle, source) DO NOTHING
RETURNING id
"""
COLS = "id, created_at, alert_time, asset_id, source, level, cycle, sop_code, llm_used, fallback_reason, status, text, payload"


async def init_workorder_table(pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE)
    logger.info("work_orders 테이블이 준비되었습니다.")


async def insert_work_order(pool, wo: dict):
    """저장하면 새 id, 같은 알림의 지시서가 이미 있으면 None."""
    async with pool.acquire() as conn:
        return await conn.fetchval(INSERT, _parse_ts(wo.get("alert_time")), wo["asset_id"], wo.get("source"), wo["level"],
                                   wo.get("cycle"), wo.get("sop_code"), bool(wo.get("llm_used")), wo.get("fallback_reason"),
                                   wo.get("text"), json.dumps(wo, ensure_ascii=False, default=str))


def _row(r) -> dict:
    d = dict(r)
    if isinstance(d.get("payload"), str):
        d["payload"] = json.loads(d["payload"])
    return d


async def fetch_work_orders(pool, limit: int = 50, asset_id: str | None = None, level: str | None = None):
    conds, args = [], []
    if asset_id:
        args.append(asset_id); conds.append(f"asset_id = ${len(args)}")
    if level:
        args.append(level); conds.append(f"level = ${len(args)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    args.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {COLS} FROM work_orders {where} ORDER BY created_at DESC, id DESC LIMIT ${len(args)}", *args)
    return [_row(r) for r in rows]


async def fetch_work_order(pool, wid: int):
    async with pool.acquire() as conn:
        r = await conn.fetchrow(f"SELECT {COLS} FROM work_orders WHERE id = $1", wid)
    return _row(r) if r else None


async def find_work_order(pool, asset_id: str, level: str, cycle: int | None, source: str | None):
    async with pool.acquire() as conn:
        r = await conn.fetchrow(f"SELECT {COLS} FROM work_orders WHERE asset_id=$1 AND level=$2 AND cycle IS NOT DISTINCT FROM $3 "
                                f"AND source IS NOT DISTINCT FROM $4 LIMIT 1", asset_id, level, cycle, source)
    return _row(r) if r else None


async def workorder_summary(pool) -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT llm_used, count(*) AS c FROM work_orders GROUP BY llm_used")
        reasons = await conn.fetch("SELECT fallback_reason, count(*) AS c FROM work_orders WHERE NOT llm_used GROUP BY fallback_reason ORDER BY c DESC")
    by = {bool(r["llm_used"]): int(r["c"]) for r in rows}
    return {"total": sum(by.values()), "llm": by.get(True, 0), "template": by.get(False, 0),
            "fallback_reasons": {r["fallback_reason"] or "-": int(r["c"]) for r in reasons}}


async def fetch_history_until(pool, vib: bool, key, until_ts, limit: int = 20):
    """알림이 생긴 시점까지의 최근 이력(시간 오름차순). vib: key=베어링 번호, 아니면 key=asset_id."""
    until = _parse_ts(until_ts)
    if vib:
        sql = ("SELECT time, file_idx, health_index, z_env_bpfo, z_env_bpfi, z_env_bsf FROM vibration_scores "
               "WHERE bearing = $1 AND time <= $2 ORDER BY time DESC LIMIT $3")
    else:
        sql = ("SELECT time, cycle, rul_pred, health_index FROM rul_predictions "
               "WHERE asset_id = $1 AND time <= $2 ORDER BY time DESC LIMIT $3")
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, key, until, limit)
    return [dict(r) for r in reversed(rows)]
