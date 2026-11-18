"""진동 추론 서비스: factory.vibration.raw 구독 → 이상 점수·Health Index 계산 → DB 저장 + 알림.

메시지 1개 = 베어링 1개의 1초 파형(20,480샘플). RUL 서비스(core/rul_service.py)와 같은 start()/stop() 패턴.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import numpy as np
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from config.settings import settings
from core.alert_engine import AlertEngine
from models.alert_repository import init_alert_table, insert_alerts
from models.database import db_manager
from ml.fault_rule import diagnose
from models.vib_repository import init_vib_tables, insert_vib

logger = logging.getLogger(__name__)
MIN_SAMPLES = 1024


def process_vibration_message(scorer, data: dict):
    """메시지 1건 → (점수 행, 스펙트럼 행) 또는 None(처리 불가)."""
    try:
        bearing = int(data["bearing"])
        x = np.asarray(data["samples"], dtype=float)
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("진동 메시지 스킵(형식 오류): %s", e)
        return None
    if int(data.get("fs", scorer.fs)) != scorer.fs or x.ndim != 1 or len(x) < MIN_SAMPLES:
        logger.warning("진동 메시지 스킵(fs/길이 불일치): fs=%s len=%s", data.get("fs"), getattr(x, "shape", None))
        return None
    try:
        r = scorer.process(bearing, x)
    except ValueError as e:
        logger.warning("진동 메시지 스킵: %s", e)
        return None
    spec = r.pop("spec")
    common = {"time": data.get("ts"), "asset_id": data.get("asset_id") or f"ims_bearing_{bearing}",
              "bearing": bearing, "file_idx": data.get("file_idx"), "elapsed_h": data.get("elapsed_h")}
    return {**common, **{k: v for k, v in r.items() if k != "bearing"}}, {**common, "spec": spec}


async def handle_vib_batch(batch, scorer, alert_engine, db_pool, producer, insert_fn=insert_vib, alert_insert_fn=insert_alerts):
    scores, spectra, alerts = [], [], []
    for _tp, msgs in batch.items():
        for m in msgs:
            out = process_vibration_message(scorer, m.value)
            if out is None:
                continue
            srow, prow = out
            scores.append(srow); spectra.append(prow)
            a = alert_engine.evaluate(srow["asset_id"], srow.get("file_idx"), srow["health_index"], None, srow.get("time"))
            if a:
                a["source"] = m.value.get("source", "ims_set2")
                d = diagnose(srow.get("z_env_bpfo"), srow.get("z_env_bpfi"), srow.get("z_env_bsf"),
                             getattr(settings, "FAULT_MIN_Z", 3.0), getattr(settings, "FAULT_MARGIN", 1.5))
                a["message"] += f" · 의심 결함: {d['label']}"
                alerts.append(a)
    if scores:
        await insert_fn(db_pool, scores, spectra)
    if alerts:
        await alert_insert_fn(db_pool, alerts)
        topic = getattr(settings, "KAFKA_TOPIC_ALERTS", "factory.alerts")
        for a in alerts:
            await producer.send_and_wait(topic, value=a, key=a["asset_id"])
        logger.warning("진동 알림 %d건: %s", len(alerts), "; ".join(a["message"] for a in alerts[:3]))
    return len(scores)


class VibrationInferenceService:
    def __init__(self):
        self.scorer = None
        self.consumer = None
        self.producer = None
        self.task: asyncio.Task | None = None
        self.processed = 0
        self.error: str | None = None
        self.started = False
        self.alert_engine = AlertEngine(getattr(settings, "ALERT_WARN_HI", 60.0), getattr(settings, "ALERT_CRIT_HI", 30.0),
                                        getattr(settings, "ALERT_HYSTERESIS", 5.0))

    # 테스트에서 바꿔 끼울 수 있도록 분리
    def _load_scorer(self):
        from ml.ims_scorer import IMSScorer
        return IMSScorer.from_files(settings.ML_ARTIFACT_DIR)

    def _make_consumer(self):
        return AIOKafkaConsumer(
            settings.KAFKA_TOPIC_VIBRATION_RAW, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id="vib-inference", value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            auto_offset_reset="earliest", enable_auto_commit=False)

    def _make_producer(self):
        return AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, key_serializer=lambda k: k.encode("utf-8"),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"))

    async def start(self):
        if not db_manager.pool:
            raise RuntimeError("db_manager.connect() 가 먼저 호출되어야 합니다.")
        self.scorer = await asyncio.to_thread(self._load_scorer)
        await init_vib_tables(db_manager.pool)
        await init_alert_table(db_manager.pool)
        self.consumer, self.producer = self._make_consumer(), self._make_producer()
        await self.consumer.start()
        await self.producer.start()
        self.started = True
        self.task = asyncio.create_task(self._loop())
        logger.info("진동 추론 서비스 시작")

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

    def reset(self):
        """리플레이 초기화 시: 평활 이력과 알림 단계를 비운다."""
        if self.scorer is not None:
            self.scorer.reset()
        self.alert_engine.reset()
        self.processed = 0

    async def _loop(self):
        try:
            while True:
                batch = await self.consumer.getmany(timeout_ms=1000, max_records=20)
                if not batch:
                    continue
                n = await handle_vib_batch(batch, self.scorer, self.alert_engine, db_manager.pool, self.producer)
                await self.consumer.commit()
                self.processed += n
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = str(e)
            logger.exception("진동 추론 루프 오류")

    def status(self) -> dict:
        return {"running": bool(self.task and not self.task.done()), "started": self.started,
                "processed_since_start": self.processed, "error": self.error}
