from contextlib import asynccontextmanager
import asyncio
import contextlib
import logging
from fastapi import FastAPI, HTTPException

from config.settings import settings
from models.database import db_manager
from core.producer import SensorDataProducer
from core.consumer import SensorDataConsumer
from core.replay_service import ReplayService
from core.rul_service import RULInferenceService
from api import replay as replay_api
from api import rul as rul_api
from api import alerts as alerts_api

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# 전역 인스턴스 (가상 데이터 파이프라인,  실제 데이터셋 파이프라인)
producer = SensorDataProducer()
consumer = SensorDataConsumer()

replay_service = ReplayService()
rul_service = RULInferenceService()

# 백그라운드 태스크는 참조를 보관해야 중간에 정리되지 않고, 종료 때 취소할 수 있다
background_tasks: list[asyncio.Task] = []


@asynccontextmanager # 작업의 준비(시작)부터 정리(끝)을 비동기로 묶는 도구 (fastAPI의 경우 서버 온오프 시 해야할일을 구성함))
async def lifespan(app: FastAPI):
    """FastAPI 생명주기(Lifespan) 관리: 앱 시작 시 DB/Kafka 구동, 종료 시 안전 해제"""
    # [1] 시작 시 실행 (Startup)
    logger.info("FastAPI 서버 시작 중: 인프라 연결 및 백그라운드 파이프라인 구동...")

    try:
        # 1. TimescaleDB 연결 및 테이블 초기화
        await db_manager.connect()
        await db_manager.init_tables()

        # 2. (가상 데이터) Kafka Producer/Consumer + 백그라운드 루프 — .env 의 ENABLE_VIRTUAL_DATA 로 켜고 끔
        if settings.ENABLE_VIRTUAL_DATA:
            await producer.start()
            await consumer.start()
            background_tasks.append(asyncio.create_task(producer.produce_loop(interval=2.0)))  # 2초마다 가상 데이터 생성
            background_tasks.append(asyncio.create_task(consumer.consume_loop()))
            logger.info("가상 데이터 파이프라인이 실행되었습니다.")
        else:
            logger.info("가상 데이터 파이프라인은 비활성화되어 있습니다. (ENABLE_VIRTUAL_DATA=false)")

        # 3. (실제 데이터) 리플레이 서비스: 준비만 해 두고, 시작은 API(버튼)로
        await replay_service.start()

        # 4. (실제 데이터) RUL 추론 서비스: 모델/파일 문제가 있어도 서버 전체는 뜨도록 보호
        if settings.ENABLE_RUL_SERVICE:
            try:
                await rul_service.start()
            except Exception as e:
                rul_service.error = str(e)
                logger.error(f"RUL 추론 서비스 시작 실패 (서버는 계속 실행됩니다): {e}")

        app.state.replay_service = replay_service
        app.state.rul_service = rul_service

        logger.info("서버 초기화가 완료되었습니다.")
    except Exception as e:
        logger.error(f"서버 초기화 중 치명적 에러 발생: {e}")
        raise

    yield  # 서버 실행되는 동안 여기서 대기

    # [2] 종료 시 실행 (Shutdown)
    logger.info("FastAPI 서버 종료 중: 리소스 정리...")
    for t in background_tasks:
        t.cancel()
    for t in background_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t
    background_tasks.clear()

    await rul_service.stop()
    await replay_service.stop()
    await consumer.stop()
    await producer.stop()
    await db_manager.disconnect()
    logger.info("모든 리소스가 안전하게 해제되었습니다.")

# FastAPI 앱 생성 (lifespan 적용)
app = FastAPI(
    title="Smart Factory PdM Pipeline", # api 이름 
    description="FastAPI + Kafka + TimescaleDB 실시간 예지보전 시스템", # api 상세설명
    version="1.1.0",
    lifespan=lifespan # 서버 할일
)

app.include_router(replay_api.router)
app.include_router(rul_api.router)
app.include_router(alerts_api.router)  


@app.get("/") # url로 들어오는 HTTP 통신 방식 중 GET 요청 처리 , 가장 기본 주소
async def root():
    """서버 상태 확인용 헬스체크 엔드포인트"""
    return {
        "status": "running",
        "service": "Smart Factory PdM Pipeline",
        "database_connected": db_manager.pool is not None, # db_manager.pool 비어있는지 여부
        "virtual_data": settings.ENABLE_VIRTUAL_DATA,
        "rul_service": rul_service.status(),
        "replay": replay_service.status(),
    }

@app.get("/api/v1/sensors/latest") # 최신 데이터 요청 시 처리 데이터 던져줄 엔드포인트
async def get_latest_sensors(limit: int = 10):
    """TimescaleDB에 적재된 최근 센서 데이터 조회 엔드포인트"""
    if not db_manager.pool:
        raise HTTPException(status_code=500, detail="Database pool is not initialized")  # (수정) db_detail → detail

    query = """
    SELECT time, machine_id, sensor_type, value, status_code
    FROM sensor_readings
    ORDER BY time DESC
    LIMIT $1;
    """

    try:
        async with db_manager.pool.acquire() as connection: # 연결된 데이터베이스에서 데이터 통로 하나 빌려쓴다.
            rows = await connection.fetch(query, limit) # 쿼리 실행한 결과 받기
            # asyncpg의 Record 객체를 파이썬 딕셔너리 리스트로 변환
            results = [dict(row) for row in rows]
            return {"count": len(results), "data": results}
    except Exception as e:
        logger.error(f"센서 데이터 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
