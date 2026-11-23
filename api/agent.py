"""작업 지시서 조회 API."""
from fastapi import APIRouter, HTTPException, Request

from models.database import db_manager
from models.workorder_repository import fetch_work_order, fetch_work_orders, find_work_order, workorder_summary

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _pool():
    if not db_manager.pool:
        raise HTTPException(status_code=503, detail="Database pool is not initialized")
    return db_manager.pool


@router.get("/summary")
async def summary(request: Request):
    svc = getattr(request.app.state, "agent_service", None)
    return {**(await workorder_summary(_pool())), "agent_service": svc.status() if svc else None}


@router.get("/workorders")
async def list_workorders(limit: int = 50, asset_id: str | None = None, level: str | None = None):
    if level not in (None, "warning", "critical"):
        raise HTTPException(status_code=400, detail="level 은 warning 또는 critical")
    rows = await fetch_work_orders(_pool(), min(max(limit, 1), 300), asset_id, level)
    return {"count": len(rows), "data": rows}


@router.get("/workorders/{wid}")
async def get_workorder(wid: int):
    row = await fetch_work_order(_pool(), wid)
    if not row:
        raise HTTPException(status_code=404, detail="작업 지시서가 없습니다.")
    return row


@router.get("/by-alert")
async def by_alert(asset_id: str, level: str, cycle: int | None = None, source: str | None = None):
    """알림(설비, 단계, 사이클, 출처)에 대응하는 작업 지시서."""
    row = await find_work_order(_pool(), asset_id, level, cycle, source)
    return {"data": row}
