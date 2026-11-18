"""진동 점수/스펙트럼 저장·조회 (asyncpg). db_manager.pool 을 그대로 사용한다."""
from __future__ import annotations

import logging

from models.rul_repository import _parse_ts

logger = logging.getLogger(__name__)

CREATE_SCORES = """
CREATE TABLE IF NOT EXISTS vibration_scores (
    time          TIMESTAMPTZ NOT NULL,
    asset_id      VARCHAR(50) NOT NULL,
    bearing       INT NOT NULL,
    file_idx      INT,
    elapsed_h     DOUBLE PRECISION,
    ae_score      DOUBLE PRECISION,
    ratio         DOUBLE PRECISION,
    health_raw    DOUBLE PRECISION,
    health_index  DOUBLE PRECISION,
    rms           DOUBLE PRECISION,
    kurtosis      DOUBLE PRECISION,
    z_env_bpfo    DOUBLE PRECISION,
    z_env_bpfi    DOUBLE PRECISION,
    z_env_bsf     DOUBLE PRECISION
);
"""
CREATE_SPECTRA = """
CREATE TABLE IF NOT EXISTS vibration_spectra (
    time       TIMESTAMPTZ NOT NULL,
    asset_id   VARCHAR(50) NOT NULL,
    bearing    INT NOT NULL,
    file_idx   INT,
    elapsed_h  DOUBLE PRECISION,
    spec       REAL[] NOT NULL
);
"""
SETUP = [
    "CREATE EXTENSION IF NOT EXISTS timescaledb;",
    "SELECT create_hypertable('vibration_scores', 'time', if_not_exists => TRUE);",
    "SELECT create_hypertable('vibration_spectra', 'time', if_not_exists => TRUE);",
    "CREATE INDEX IF NOT EXISTS idx_vib_scores_b ON vibration_scores (bearing, time DESC);",
    "CREATE INDEX IF NOT EXISTS idx_vib_spectra_b ON vibration_spectra (bearing, time DESC);",
]
INSERT_SCORE = """
INSERT INTO vibration_scores (time, asset_id, bearing, file_idx, elapsed_h, ae_score, ratio, health_raw,
                              health_index, rms, kurtosis, z_env_bpfo, z_env_bpfi, z_env_bsf)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
"""
INSERT_SPEC = "INSERT INTO vibration_spectra (time, asset_id, bearing, file_idx, elapsed_h, spec) VALUES ($1,$2,$3,$4,$5,$6)"


async def init_vib_tables(pool):
    async with pool.acquire() as conn:
        await conn.execute(CREATE_SCORES)
        await conn.execute(CREATE_SPECTRA)
        for q in SETUP:
            try:
                await conn.execute(q)
            except Exception as e:                  # extension 권한/이미 존재 등은 경고만
                logger.warning("진동 테이블 설정 스킵: %s", e)
    logger.info("vibration_scores / vibration_spectra 테이블이 준비되었습니다.")


async def insert_vib(pool, scores: list[dict], spectra: list[dict]) -> int:
    if not scores:
        return 0
    srecs = [(_parse_ts(r.get("time")), r["asset_id"], r["bearing"], r.get("file_idx"), r.get("elapsed_h"),
              r.get("ae_score"), r.get("ratio"), r.get("health_raw"), r.get("health_index"), r.get("rms"),
              r.get("kurtosis"), r.get("z_env_bpfo"), r.get("z_env_bpfi"), r.get("z_env_bsf")) for r in scores]
    precs = [(_parse_ts(r.get("time")), r["asset_id"], r["bearing"], r.get("file_idx"), r.get("elapsed_h"), r["spec"])
             for r in spectra]
    async with pool.acquire() as conn:
        await conn.executemany(INSERT_SCORE, srecs)
        if precs:
            await conn.executemany(INSERT_SPEC, precs)
    return len(srecs)


async def fetch_vib_latest(pool):
    """베어링별 가장 최근 점수 1건 + 마지막 수신 후 경과 시간(age_sec)."""
    sql = """
    SELECT DISTINCT ON (bearing) bearing, asset_id, time, file_idx, elapsed_h, ae_score, ratio, health_raw,
           health_index, rms, kurtosis, z_env_bpfo, z_env_bpfi, z_env_bsf,
           EXTRACT(EPOCH FROM (now() - time))::float8 AS age_sec
    FROM vibration_scores ORDER BY bearing, time DESC
    """
    async with pool.acquire() as conn:
        return await conn.fetch(sql)


async def fetch_vib_history(pool, bearing: int, limit: int = 1000):
    sql = """
    SELECT time, file_idx, elapsed_h, ratio, health_raw, health_index, rms, kurtosis, z_env_bpfo, z_env_bpfi, z_env_bsf
    FROM vibration_scores WHERE bearing = $1 ORDER BY time DESC LIMIT $2
    """
    async with pool.acquire() as conn:
        return await conn.fetch(sql, bearing, limit)


async def fetch_vib_spectra(pool, bearing: int, limit: int = 1):
    sql = "SELECT time, file_idx, elapsed_h, spec FROM vibration_spectra WHERE bearing = $1 ORDER BY time DESC LIMIT $2"
    async with pool.acquire() as conn:
        return await conn.fetch(sql, bearing, limit)


async def vib_counts(pool) -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetchval("SELECT count(*) FROM vibration_scores")
        bearings = await conn.fetchval("SELECT count(DISTINCT bearing) FROM vibration_scores")
    return {"rows": int(rows), "bearings": int(bearings)}
