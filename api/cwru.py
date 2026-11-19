"""CWRU 결함 분류 결과 조회 API."""
from fastapi import APIRouter, HTTPException, Request

from models.cwru_repository import cwru_counts, fetch_cwru_files, fetch_cwru_latest, fetch_cwru_recent
from models.database import db_manager

router = APIRouter(prefix="/api/v1/cwru", tags=["cwru"])


def _pool():
    if not db_manager.pool:
        raise HTTPException(status_code=503, detail="Database pool is not initialized")
    return db_manager.pool


@router.get("/config")
async def config(request: Request):
    svc = getattr(request.app.state, "cwru_service", None)
    clf = getattr(svc, "clf", None)
    return {"classes": clf.classes if clf else None, "window": clf.window if clf else None,
            "trained_on": (clf.meta.get("trained_on") if clf else None)}


@router.get("/summary")
async def summary(request: Request):
    svc = getattr(request.app.state, "cwru_service", None)
    return {**(await cwru_counts(_pool())), "cwru_service": svc.status() if svc else None}


@router.get("/latest")
async def latest():
    row = await fetch_cwru_latest(_pool())
    return {"data": dict(row) if row else None}


@router.get("/files")
async def files():
    """파일별 정답 대비 분류 정확도와 예측 분포."""
    rows, dist = await fetch_cwru_files(_pool())
    dd = {}
    for r in dist:
        dd.setdefault(r["file_id"], {})[r["pred_label"]] = int(r["c"])
    data = []
    for r in rows:
        d = dict(r)
        n = max(int(d["n"]), 1)
        d["accuracy"] = d["correct"] / n
        d["vote_accuracy"] = d["vote_correct"] / n
        d["dist"] = dd.get(d["file_id"], {})
        data.append(d)
    return {"count": len(data), "data": data}


@router.get("/recent")
async def recent(limit: int = 50):
    rows = await fetch_cwru_recent(_pool(), min(max(limit, 1), 500))
    return {"count": len(rows), "data": [dict(r) for r in rows]}
