"""RUL 예측 조회 API (대시보드용)."""
from fastapi import APIRouter, HTTPException, Request

from models.database import db_manager
from models.rul_repository import fetch_latest_rul, fetch_rul_history

router = APIRouter(prefix="/api/v1/rul", tags=["rul"])


def _pool():
    if not db_manager.pool:
        raise HTTPException(status_code=503, detail="Database pool is not initialized")
    return db_manager.pool


@router.get("/latest")
async def latest():
    """엔진별 가장 최근 예측 1건."""
    rows = await fetch_latest_rul(_pool())
    data = [dict(r) for r in rows]
    return {"count": len(data), "data": data}


@router.get("/history/{asset_id}")
async def history(asset_id: str, limit: int = 500):
    """특정 엔진의 예측 이력(오래된 순)."""
    rows = await fetch_rul_history(_pool(), asset_id, min(max(limit, 1), 5000))
    data = [dict(r) for r in reversed(rows)]
    return {"asset_id": asset_id, "count": len(data), "data": data}


@router.get("/summary")
async def summary(request: Request):
    """저장 건수 + 서비스 상태 (대시보드 상단 요약)."""
    async with _pool().acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM rul_predictions")
        engines = await conn.fetchval("SELECT count(DISTINCT asset_id) FROM rul_predictions")
    return {
        "rows": total, "engines": engines,
        "rul_service": request.app.state.rul_service.status(),
        "replay": request.app.state.replay_service.status(),
    }
