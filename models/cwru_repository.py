"""CWRU 분류 결과 저장·조회 (asyncpg). db_manager.pool 을 그대로 사용한다."""
from __future__ import annotations

import logging

from models.rul_repository import _parse_ts

logger = logging.getLogger(__name__)

CREATE = """
CREATE TABLE IF NOT EXISTS cwru_predictions (
    time        TIMESTAMPTZ NOT NULL,
    asset_id    VARCHAR(50) NOT NULL,
    file_id     INT,
    win_idx     INT,
    load_hp     INT,
    severity    DOUBLE PRECISION,
    rpm         DOUBLE PRECISION,
    label_true  VARCHAR(10),
    pred_label  VARCHAR(10),
    vote_label  VARCHAR(10),
    proba       REAL[]
);
"""
SETUP = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb;",
    "SELECT create_hypertable('cwru_predictions', 'time', if_not_exists => TRUE);",
    "CREATE INDEX IF NOT EXISTS idx_cwru_file ON cwru_predictions (file_id, time DESC);",
]
INSERT = """
INSERT INTO cwru_predictions (time, asset_id, file_id, win_idx, load_hp, severity, rpm, label_true, pred_label, vote_label, proba)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
"""


async def init_cwru_tables(pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE)
        for q in SETUP:
            try:
                await conn.execute(q)
            except Exception as e:
                logger.warning("CWRU 테이블 설정 스킵: %s", e)
    logger.info("cwru_predictions 테이블이 준비되었습니다.")


async def insert_cwru(pool, rows: list[dict]) -> int:
    if not rows:
        return 0
    recs = [(_parse_ts(r.get("time")), r["asset_id"], r.get("file_id"), r.get("win_idx"), r.get("load_hp"), r.get("severity"),
             r.get("rpm"), r.get("label_true"), r.get("pred_label"), r.get("vote_label"), r["proba"]) for r in rows]
    async with pool.acquire() as conn:
        await conn.executemany(INSERT, recs)
    return len(recs)


async def fetch_cwru_latest(pool):
    sql = """
    SELECT time, asset_id, file_id, win_idx, load_hp, severity, rpm, label_true, pred_label, vote_label, proba,
           EXTRACT(EPOCH FROM (now() - time))::float8 AS age_sec
    FROM cwru_predictions ORDER BY time DESC LIMIT 1
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql)


async def fetch_cwru_files(pool):
    """파일별 요약: 윈도우 수, 단일 윈도우 정확도, 투표(최근 10개 평균) 정확도, 예측 분포."""
    sql = """
    SELECT file_id, min(label_true) AS label_true, min(load_hp) AS load_hp, min(severity) AS severity,
           count(*) AS n, count(*) FILTER (WHERE pred_label = label_true) AS correct,
           count(*) FILTER (WHERE vote_label = label_true) AS vote_correct, min(time) AS first_time
    FROM cwru_predictions GROUP BY file_id ORDER BY min(time)
    """
    dist = "SELECT file_id, pred_label, count(*) AS c FROM cwru_predictions GROUP BY file_id, pred_label"
    async with pool.acquire() as conn:
        return await conn.fetch(sql), await conn.fetch(dist)


async def fetch_cwru_recent(pool, limit: int = 50):
    sql = "SELECT time, file_id, win_idx, label_true, pred_label, vote_label FROM cwru_predictions ORDER BY time DESC LIMIT $1"
    async with pool.acquire() as conn:
        return await conn.fetch(sql, limit)


async def cwru_counts(pool) -> dict:
    async with pool.acquire() as conn:
        return {"rows": int(await conn.fetchval("SELECT count(*) FROM cwru_predictions")),
                "files": int(await conn.fetchval("SELECT count(DISTINCT file_id) FROM cwru_predictions"))}
