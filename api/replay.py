"""리플레이 제어 API (Streamlit 버튼/Swagger에서 호출)."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from models.alert_repository import init_alert_table
from models.database import db_manager

router = APIRouter(prefix="/api/v1/replay", tags=["replay"])


class ReplayRequest(BaseModel):
    kind: Literal["cycle", "vibration"] = "cycle"
    interval: float = Field(0.05, ge=0.0, le=5.0, description="메시지 간 대기(초). 0.005=빠르게, 0.05=보통")
    loop: bool = Field(False, description="끝나면 처음부터 반복")


@router.post("/start")
async def start_replay(req: ReplayRequest, request: Request):
    svc = request.app.state.replay_service
    try:
        await svc.begin(req.kind, req.interval, req.loop)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return svc.status()


@router.post("/stop")
async def stop_replay(request: Request):
    svc = request.app.state.replay_service
    await svc.cancel()
    return svc.status()


@router.get("/status")
async def replay_status(request: Request):
    return request.app.state.replay_service.status()


@router.post("/reset")
async def reset_predictions(request: Request, confirm: bool = False):
    """rul_predictions, alerts 테이블의 모든 행을 삭제하고 알림 상태를 초기화. ?confirm=true 필요."""
    if not confirm:
        raise HTTPException(status_code=400, detail="삭제하려면 ?confirm=true 를 붙이세요.")
    if request.app.state.replay_service.is_running():
        raise HTTPException(status_code=409, detail="리플레이 실행 중에는 초기화할 수 없습니다. 먼저 중지하세요.")
    if not db_manager.pool:
        raise HTTPException(status_code=503, detail="Database pool is not initialized")
    await init_alert_table(db_manager.pool)                  # 테이블이 없을 수 있으므로 보장
    async with db_manager.pool.acquire() as conn:
        await conn.execute("TRUNCATE rul_predictions")
        await conn.execute("TRUNCATE alerts")
    request.app.state.rul_service.alert_engine.reset()       # 엔진별 알림 단계도 초기화
    return {"reset": True}
