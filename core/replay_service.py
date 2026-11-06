"""실제 데이터셋 리플레이를 FastAPI 서버 안에서 시작/중지/상태조회 할 수 있게 감싼 서비스.

기존 SensorDataProducer 와 같은 start()/stop() 패턴. 리플레이 자체는 API 호출(버튼)로만 시작된다.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from aiokafka import AIOKafkaProducer

from config.settings import settings
from core.replay_producer import iter_cycle_messages, iter_vibration_messages

logger = logging.getLogger(__name__)

VIB_CHUNKS_PER_FILE = 10      # 20,480샘플 / 2048


class ReplayService:
    def __init__(self):
        self.producer: AIOKafkaProducer | None = None
        self.task: asyncio.Task | None = None
        self.state: dict = {"status": "idle", "kind": None, "sent": 0, "total": 0,
                            "interval": None, "loop": False, "error": None, "started_at": None}

    # ---- 생명주기 ----
    def _make_producer(self):
        return AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            key_serializer=lambda k: k.encode("utf-8"),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

    async def start(self):
        self.producer = self._make_producer()
        await self.producer.start()
        logger.info("ReplayService 준비 완료 (대기 중)")

    async def stop(self):
        await self.cancel()
        if self.producer:
            await self.producer.stop()
            self.producer = None

    # ---- 제어 ----
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    @staticmethod
    def _resolve(kind: str):
        if kind == "cycle":
            return Path(settings.CMAPSS_DATA_DIR), settings.KAFKA_TOPIC_CYCLE_RAW, iter_cycle_messages
        if kind == "vibration":
            return Path(settings.IMS_DATA_DIR), settings.KAFKA_TOPIC_VIBRATION_RAW, iter_vibration_messages
        raise ValueError(f"알 수 없는 kind: {kind}")

    @staticmethod
    def _count_total(kind: str, data_dir: Path) -> int:
        if kind == "cycle":
            with open(data_dir / "test_FD001.txt", "rb") as f:
                return sum(1 for line in f if line.strip())
        return sum(1 for p in data_dir.iterdir() if p.is_file()) * VIB_CHUNKS_PER_FILE

    async def begin(self, kind: str, interval: float, loop_forever: bool = False):
        if self.is_running():
            raise RuntimeError("이미 리플레이가 실행 중입니다.")
        if not self.producer:
            raise RuntimeError("ReplayService가 시작되지 않았습니다.")
        data_dir, topic, gen_fn = self._resolve(kind)
        if not data_dir.is_dir():
            raise ValueError(f"데이터 폴더를 찾을 수 없습니다: {data_dir}")
        if kind == "cycle" and not (data_dir / "test_FD001.txt").exists():
            raise ValueError(f"test_FD001.txt 가 없습니다: {data_dir}")

        self.state = {"status": "running", "kind": kind, "sent": 0,
                      "total": self._count_total(kind, data_dir), "interval": interval,
                      "loop": loop_forever, "error": None,
                      "started_at": datetime.now(timezone.utc).isoformat()}
        self.task = asyncio.create_task(self._run(topic, gen_fn, data_dir, interval, loop_forever))

    async def cancel(self):
        if self.is_running():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        self.task = None

    async def _run(self, topic, gen_fn, data_dir, interval, loop_forever):
        try:
            while True:
                self.state["sent"] = 0
                for msg in gen_fn(data_dir):
                    await self.producer.send_and_wait(topic, value=msg, key=msg["asset_id"])
                    self.state["sent"] += 1
                    await asyncio.sleep(interval)
                if not loop_forever:
                    break
            self.state["status"] = "finished"
            logger.info("리플레이 완료")
        except asyncio.CancelledError:
            self.state["status"] = "stopped"
            logger.info("리플레이 중지됨")
            raise
        except Exception as e:
            self.state["status"] = "error"
            self.state["error"] = str(e)
            logger.exception("리플레이 중 오류")

    def status(self) -> dict:
        return dict(self.state)
