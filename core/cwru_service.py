"""CWRU 결함 분류 서비스: factory.cwru.raw 구독 → Random Forest(numpy) 분류 → DB 저장.

메시지 1개 = 2048샘플 윈도우. 파일(asset)별로 최근 10개 윈도우의 확률 평균으로 '투표' 결과를 만들어 화면이 덜 깜빡이게 한다.
IMS 신호에는 쓰지 않는다(베어링 모델·샘플링 주파수가 다름).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import defaultdict, deque

import numpy as np
from aiokafka import AIOKafkaConsumer

from config.settings import settings
from models.cwru_repository import init_cwru_tables, insert_cwru
from models.database import db_manager

logger = logging.getLogger(__name__)
VOTE_WINDOWS = 10


def process_cwru_message(clf, data: dict):
    """메시지 1건 → 결과 행(vote_label 은 호출한 쪽에서 채움) 또는 None."""
    try:
        samples = data["samples"]
        rpm = float(data["rpm"])
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("CWRU 메시지 스킵(형식 오류): %s", e)
        return None
    if int(data.get("fs", clf.fs)) != clf.fs or len(samples) != clf.window:
        logger.warning("CWRU 메시지 스킵(fs/윈도우 길이 불일치): fs=%s len=%s", data.get("fs"), len(samples))
        return None
    r = clf.process(samples, rpm)
    return {"time": data.get("ts"), "asset_id": data.get("asset_id") or f"cwru_{data.get('file_id')}",
            "file_id": data.get("file_id"), "win_idx": data.get("win_idx"), "load_hp": data.get("load_hp"),
            "severity": data.get("severity"), "rpm": rpm, "label_true": data.get("label_true"),
            "pred_label": r["label"], "proba": r["proba"]}


async def handle_cwru_batch(batch, clf, votes: dict, db_pool, insert_fn=insert_cwru):
    rows = []
    for _tp, msgs in batch.items():
        for m in msgs:
            row = process_cwru_message(clf, m.value)
            if row is None:
                continue
            dq = votes.setdefault(row["asset_id"], deque(maxlen=VOTE_WINDOWS))
            dq.append(row["proba"])
            row["vote_label"] = clf.classes[int(np.mean(dq, axis=0).argmax())]
            rows.append(row)
    if rows:
        await insert_fn(db_pool, rows)
    return len(rows)


class CWRUClassifierService:
    def __init__(self):
        self.clf = None
        self.consumer = None
        self.task: asyncio.Task | None = None
        self.votes: dict = defaultdict(lambda: deque(maxlen=VOTE_WINDOWS))
        self.processed = 0
        self.error: str | None = None
        self.started = False

    def _load(self):
        from ml.cwru_classifier import CWRUClassifier
        return CWRUClassifier.from_file(f"{settings.ML_ARTIFACT_DIR}/cwru_rf.npz")

    def _make_consumer(self):
        return AIOKafkaConsumer(
            getattr(settings, "KAFKA_TOPIC_CWRU_RAW", "factory.cwru.raw"), bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id="cwru-inference", value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            auto_offset_reset="earliest", enable_auto_commit=False)

    async def start(self):
        if not db_manager.pool:
            raise RuntimeError("db_manager.connect() 가 먼저 호출되어야 합니다.")
        self.clf = await asyncio.to_thread(self._load)
        await init_cwru_tables(db_manager.pool)
        self.consumer = self._make_consumer()
        await self.consumer.start()
        self.started = True
        self.task = asyncio.create_task(self._loop())
        logger.info("CWRU 분류 서비스 시작 (classes=%s)", self.clf.classes)

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        if self.consumer:
            await self.consumer.stop()
        self.started = False

    def reset(self):
        self.votes.clear()
        self.processed = 0

    async def _loop(self):
        try:
            while True:
                batch = await self.consumer.getmany(timeout_ms=1000, max_records=50)
                if not batch:
                    continue
                n = await handle_cwru_batch(batch, self.clf, self.votes, db_manager.pool)
                await self.consumer.commit()
                self.processed += n
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = str(e)
            logger.exception("CWRU 분류 루프 오류")

    def status(self) -> dict:
        return {"running": bool(self.task and not self.task.done()), "started": self.started,
                "processed_since_start": self.processed, "error": self.error}
