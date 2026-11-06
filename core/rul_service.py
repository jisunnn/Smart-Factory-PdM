"""RUL 추론 서비스: factory.cycle.raw 구독 → 예측 → factory.rul.pred 발행 + rul_predictions 저장.

core/inference_worker.py(단독 실행용)와 같은 로직을 FastAPI 서버 안에서 start()/stop() 패턴으로 돌린다.
※ 서버 안에서 돌릴 때는 단독 워커(python -m core.inference_worker)를 동시에 실행하지 마세요(같은 그룹이 파티션을 나눠 가짐).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from config.settings import settings
from core.inference_worker import handle_batch
from models.database import db_manager
from models.rul_repository import init_rul_tables, insert_rul_predictions
from models.alert_repository import init_alert_table, insert_alerts
from core.alert_engine import AlertEngine

logger = logging.getLogger(__name__)

PRUNE_EVERY_SEC = 60


class RULInferenceService:
    def __init__(self):
        self.predictor = None
        self.consumer = None
        self.producer = None
        self.task: asyncio.Task | None = None
        self.saved = 0
        self.error: str | None = None
        self.started = False
        self.alert_engine = AlertEngine(getattr(settings, "ALERT_WARN_HI", 60.0), getattr(settings, "ALERT_CRIT_HI", 30.0),
                                  getattr(settings, "ALERT_HYSTERESIS", 5.0))

    # 테스트에서 바꿔 끼울 수 있도록 분리
    def _load_predictor(self):
        from ml.rul_model import RULPredictor          # torch는 여기서만 import
        return RULPredictor(settings.ML_ARTIFACT_DIR, settings.RUL_MODEL_FILES)

    def _make_consumer(self):
        return AIOKafkaConsumer(
            settings.KAFKA_TOPIC_CYCLE_RAW,
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id="rul-inference",
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            auto_offset_reset="earliest",
            enable_auto_commit=False,                  # DB 저장 성공 후에만 수동 커밋
        )

    def _make_producer(self):
        return AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            key_serializer=lambda k: k.encode("utf-8"),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

    async def start(self):
        if not db_manager.pool:
            raise RuntimeError("db_manager.connect() 가 먼저 호출되어야 합니다.")
        self.predictor = await asyncio.to_thread(self._load_predictor)   # 모델 로딩은 별도 스레드
        await init_rul_tables(db_manager.pool)
        await init_alert_table(db_manager.pool)
        self.consumer = self._make_consumer()
        self.producer = self._make_producer()
        await self.consumer.start()
        await self.producer.start()
        self.started = True
        self.task = asyncio.create_task(self._loop())
        logger.info("RUL 추론 서비스 시작 (models=%d)", len(self.predictor.models))

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()
        self.started = False
        logger.info("RUL 추론 서비스 종료")

    def warmup(self) -> dict:
        """윈도우가 아직 덜 찬(예측 대기 중인) 엔진 → 모인 사이클 수."""
        return self.predictor.warmup() if self.predictor and hasattr(self.predictor, "warmup") else {}

    def _prune(self):
        idle = getattr(settings, "ENGINE_IDLE_PRUNE_SEC", 3600)
        n = self.alert_engine.prune(idle)
        if self.predictor is not None and hasattr(self.predictor, "prune"):
            n += self.predictor.prune(idle)
        if n:
            logger.info("오래 데이터가 없는 엔진 상태 %d건을 정리했습니다. (기준 %ds)", n, idle)

    async def _loop(self):
        last_prune = time.monotonic()
        try:
            while True:
                if time.monotonic() - last_prune > PRUNE_EVERY_SEC:
                    self._prune()
                    last_prune = time.monotonic()
                batch = await self.consumer.getmany(timeout_ms=1000, max_records=500)
                if not batch:
                    continue
                n = await handle_batch(batch, self.predictor, self.producer, db_manager.pool,
                                       insert_rul_predictions, self.alert_engine, insert_alerts)
                await self.consumer.commit()
                self.saved += n
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = str(e)
            logger.exception("RUL 추론 루프 오류")

    def status(self) -> dict:
        return {"running": bool(self.task and not self.task.done()), "started": self.started,
                "saved_since_start": self.saved, "error": self.error,
                "tracked_engines": self.predictor.tracked() if self.predictor and hasattr(self.predictor, "tracked") else 0}
