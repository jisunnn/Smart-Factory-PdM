"""RUL 예측 결과 저장/조회 (asyncpg). 기존 models/database.py 는 수정하지 않고 db_manager.pool 을 그대로 사용한다."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS rul_predictions (
    time         TIMESTAMPTZ NOT NULL,
    asset_id     VARCHAR(50) NOT NULL,
    source       VARCHAR(50),
    cycle        INT,
    rul_pred     DOUBLE PRECISION NOT NULL,
    health_index DOUBLE PRECISION NOT NULL,
    rul_true     INT
);
"""
ENABLE_EXTENSION = "CREATE EXTENSION IF NOT EXISTS timescaledb;"
CREATE_HYPERTABLE = "SELECT create_hypertable('rul_predictions', 'time', if_not_exists => TRUE);"
CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_rul_asset_time ON rul_predictions (asset_id, time DESC);"

INSERT_SQL = """
INSERT INTO rul_predictions (time, asset_id, source, cycle, rul_pred, health_index, rul_true)
VALUES ($1, $2, $3, $4, $5, $6, $7)
"""


async def init_rul_tables(pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE)
        try:
            await conn.execute(ENABLE_EXTENSION)
        except Exception as e:                      # 권한 부족/이미 활성화 → 계속 진행
            logger.warning("TimescaleDB extension 활성화 스킵: %s", e)
        await conn.execute(CREATE_HYPERTABLE)
        await conn.execute(CREATE_INDEX)
    logger.info("rul_predictions 테이블(Hypertable)이 준비되었습니다.")


def _parse_ts(ts) -> datetime:
    """메시지의 ISO 문자열 → timezone-aware datetime (asyncpg는 문자열을 TIMESTAMPTZ로 받지 못함)."""
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def to_record(r: dict) -> tuple:
    return (
        _parse_ts(r.get("ts")), r["asset_id"], r.get("source"), r.get("cycle"),
        float(r["rul_pred"]), float(r["health_index"]),
        int(r["rul_true"]) if r.get("rul_true") is not None else None,
    )


async def insert_rul_predictions(pool, rows: list[dict]) -> int:
    if not rows:
        return 0
    records = [to_record(r) for r in rows]
    async with pool.acquire() as conn:
        await conn.executemany(INSERT_SQL, records)
    return len(records)


async def fetch_latest_rul(pool):
    """엔진별 가장 최근 예측 1건 (대시보드 현황판용)."""
    sql = """
    SELECT DISTINCT ON (asset_id) asset_id, time, cycle, rul_pred, health_index, rul_true,
           EXTRACT(EPOCH FROM (now() - time))::float8 AS age_sec
    FROM rul_predictions ORDER BY asset_id, time DESC
    """
    async with pool.acquire() as conn:
        return await conn.fetch(sql)


async def fetch_rul_history(pool, asset_id: str, limit: int = 500):
    """특정 엔진의 최근 예측 이력 (시계열 그래프용)."""
    sql = """
    SELECT time, cycle, rul_pred, health_index, rul_true
    FROM rul_predictions WHERE asset_id = $1 ORDER BY time DESC LIMIT $2
    """
    async with pool.acquire() as conn:
        return await conn.fetch(sql, asset_id, limit)
