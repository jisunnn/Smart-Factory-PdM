"""진동(베어링) 조회 API."""
from fastapi import APIRouter, HTTPException, Request

from config.settings import settings
from ml.fault_rule import diagnose
from models.database import db_manager
from models.vib_repository import fetch_vib_history, fetch_vib_latest, fetch_vib_spectra, vib_counts

router = APIRouter(prefix="/api/v1/vib", tags=["vibration"])
SPEC_MAX_HZ, SPEC_STEP_HZ = 1000, 2


def _pool():
    if not db_manager.pool:
        raise HTTPException(status_code=503, detail="Database pool is not initialized")
    return db_manager.pool


@router.get("/config")
async def config(request: Request):
    """결함 주파수와 스펙트럼 형식 (대시보드가 표시선/축을 그릴 때 사용)."""
    svc = getattr(request.app.state, "vib_service", None)
    scorer = getattr(svc, "scorer", None)
    return {
        "fault_hz": scorer.fault if scorer else None,
        "shaft_hz": scorer.shaft if scorer else None,
        "spectrum": {"max_hz": SPEC_MAX_HZ, "step_hz": SPEC_STEP_HZ, "kind": "envelope"},
        "warn": getattr(settings, "ALERT_WARN_HI", 60.0), "crit": getattr(settings, "ALERT_CRIT_HI", 30.0),
        "fault_rule": {"min_z": getattr(settings, "FAULT_MIN_Z", 3.0), "margin": getattr(settings, "FAULT_MARGIN", 1.5)},
    }


@router.get("/summary")
async def summary(request: Request):
    svc = getattr(request.app.state, "vib_service", None)
    return {**(await vib_counts(_pool())), "vib_service": svc.status() if svc else None}


@router.get("/latest")
async def latest():
    """베어링별 최신 점수와 마지막 수신 후 경과 시간."""
    warn = getattr(settings, "ALERT_WARN_HI", 60.0)
    min_z, margin = getattr(settings, "FAULT_MIN_Z", 3.0), getattr(settings, "FAULT_MARGIN", 1.5)
    data = [dict(r) for r in await fetch_vib_latest(_pool())]
    for d in data:      # 의심 결함은 Health Index 가 주의 기준 아래일 때만 표시
        d["suspect"] = (diagnose(d["z_env_bpfo"], d["z_env_bpfi"], d["z_env_bsf"], min_z, margin)
                        if d["health_index"] is not None and d["health_index"] < warn else None)
    return {"count": len(data), "data": data}


@router.get("/history/{bearing}")
async def history(bearing: int, limit: int = 1000):
    """특정 베어링의 이력(오래된 순, 같은 파일 번호는 마지막 기록만)."""
    rows = await fetch_vib_history(_pool(), bearing, min(max(limit, 1), 5000))
    by_file = {}
    for r in reversed(rows):                       # 오래된 것부터 → 같은 file_idx 는 뒤의 것으로 덮어씀
        by_file[r["file_idx"]] = dict(r)
    data = sorted(by_file.values(), key=lambda d: (d["file_idx"] is None, d["file_idx"]))
    return {"bearing": bearing, "count": len(data), "data": data}


@router.get("/spectrum/{bearing}")
async def spectrum(bearing: int, limit: int = 1):
    """엔벨로프 스펙트럼 (0~1000Hz, 2Hz 간격 500점). limit>1 이면 시간×주파수 히트맵용 이력."""
    rows = await fetch_vib_spectra(_pool(), bearing, min(max(limit, 1), 600))
    by_file = {}
    for r in reversed(rows):
        by_file[r["file_idx"]] = {"time": r["time"], "file_idx": r["file_idx"], "elapsed_h": r["elapsed_h"], "spec": list(r["spec"])}
    data = sorted(by_file.values(), key=lambda d: (d["file_idx"] is None, d["file_idx"]))
    return {"bearing": bearing, "max_hz": SPEC_MAX_HZ, "step_hz": SPEC_STEP_HZ, "count": len(data), "data": data}
