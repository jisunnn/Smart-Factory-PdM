"""알림 API."""
from fastapi import APIRouter, HTTPException

from config.settings import settings
from models.alert_repository import acknowledge, acknowledge_all, alert_summary, fetch_alerts
from models.database import db_manager

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


def _pool():
    if not db_manager.pool:
        raise HTTPException(status_code=503, detail="Database pool is not initialized")
    return db_manager.pool


@router.get("/config")
async def config():
    """서버가 쓰는 알림 기준 (대시보드가 같은 기준으로 색상을 표시)."""
    return {"warn": getattr(settings, "ALERT_WARN_HI", 60.0), "crit": getattr(settings, "ALERT_CRIT_HI", 30.0),
            "hysteresis": getattr(settings, "ALERT_HYSTERESIS", 5.0)}


@router.get("/summary")
async def summary():
    return await alert_summary(_pool())


@router.get("")
async def list_alerts(limit: int = 50, unacked_only: bool = False, level: str | None = None):
    if level not in (None, "warning", "critical"):
        raise HTTPException(status_code=400, detail="level 은 warning 또는 critical")
    rows = await fetch_alerts(_pool(), min(max(limit, 1), 500), unacked_only, level)
    data = [dict(r) for r in rows]
    return {"count": len(data), "data": data}


@router.post("/ack-all")
async def ack_all():
    return {"acknowledged": await acknowledge_all(_pool())}


@router.post("/{alert_id}/ack")
async def ack(alert_id: int):
    if not await acknowledge(_pool(), alert_id):
        raise HTTPException(status_code=404, detail="알림이 없거나 이미 확인되었습니다.")
    return {"acknowledged": alert_id}
