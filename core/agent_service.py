"""작업 지시서 에이전트 서비스: factory.alerts 구독 -> 컨텍스트 -> 매뉴얼 검색(RAG) -> Gemini -> 검증 -> work_orders 저장.

Gemini 키가 없거나 호출이 실패해도 템플릿 안내로 동작한다. (서버 시작을 막지 않는다)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re

from aiokafka import AIOKafkaConsumer

from agent.context import _bearing_of
from agent.gemini import GeminiClient, RateLimiter
from agent.manual import load_manual
from agent.pipeline import run_pipeline
from agent.retrieval import Retriever
from config.settings import settings
from models.database import db_manager
from models.workorder_repository import fetch_history_until, find_work_order, init_workorder_table, insert_work_order

logger = logging.getLogger(__name__)


def _cfg(name, default):
    return getattr(settings, name, os.getenv(name, default))


class WorkOrderAgentService:
    def __init__(self):
        self.manual = None
        self.retriever = None
        self.llm = None
        self.limiter = None
        self.consumer = None
        self.task: asyncio.Task | None = None
        self.processed = self.llm_count = self.template_count = 0
        self.error: str | None = None
        self.started = False
        self.model = str(_cfg("GEMINI_MODEL", "gemini-3.5-flash-lite"))

    # 테스트에서 바꿔 끼울 수 있도록 분리
    def _build(self):
        path = _cfg("MANUAL_PATH", "agent/knowledge/pdm_manual.md")
        self.manual = load_manual(path)
        key = str(_cfg("GEMINI_API_KEY", "")).strip()
        embed_model = str(_cfg("GEMINI_EMBED_MODEL", "gemini-embedding-001"))
        use_llm = str(_cfg("AGENT_USE_LLM", "true")).lower() == "true"
        client = GeminiClient(key, self.model, embed_model) if key else None
        self.llm = client if use_llm else None
        self.limiter = RateLimiter(int(_cfg("GEMINI_MAX_RPM", 10)))
        cache = os.path.join(os.path.dirname(os.path.abspath(path)), "embeddings_cache.json")
        self.retriever = Retriever(self.manual, client, embed_model if client else "none", cache)
        self.retriever.warm_up()                       # 실패하면 키워드 검색으로 자동 대체
        logger.info("에이전트 준비: 매뉴얼 조각 %d개, 검색=%s, LLM=%s", len(self.manual.chunks), self.retriever.mode,
                    "켜짐" if self.llm else "꺼짐(템플릿)")

    def _make_consumer(self):
        return AIOKafkaConsumer(
            getattr(settings, "KAFKA_TOPIC_ALERTS", "factory.alerts"), bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
            group_id="work-order-agent", value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            auto_offset_reset="latest", enable_auto_commit=False)      # 시작 전에 쌓인 알림은 처리하지 않는다

    async def start(self):
        if not db_manager.pool:
            raise RuntimeError("db_manager.connect() 가 먼저 호출되어야 합니다.")
        await asyncio.to_thread(self._build)
        await init_workorder_table(db_manager.pool)
        self.consumer = self._make_consumer()
        await self.consumer.start()
        self.started = True
        self.task = asyncio.create_task(self._loop())

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
        self.processed = self.llm_count = self.template_count = 0

    async def _history(self, alert: dict):
        try:
            vib = alert.get("source") == "ims_set2"
            key = _bearing_of(alert["asset_id"]) if vib else alert["asset_id"]
            if key is None:
                return None
            return await fetch_history_until(db_manager.pool, vib, key, alert.get("time"), 20)
        except Exception as e:                       # 이력 조회 실패는 지시서 생성을 막지 않는다
            logger.warning("이력 조회 실패: %s", e)
            return None

    async def handle_alert(self, alert: dict):
        try:                                           # 같은 알림이 다시 전달되면 LLM 호출 없이 건너뜀 (호출 한도 절약)
            if await find_work_order(db_manager.pool, alert["asset_id"], alert["level"], alert.get("cycle"), alert.get("source")):
                return None
        except Exception as e:
            logger.warning("중복 확인 실패(계속 진행): %s", e)
        history = await self._history(alert)
        wo = await asyncio.to_thread(run_pipeline, alert, history, self.retriever, self.llm, self.limiter,
                                     self.llm is not None, self.model, float(_cfg("AGENT_MAX_WAIT_SEC", 20)))
        new_id = await insert_work_order(db_manager.pool, wo)
        if new_id is not None:
            self.processed += 1
            self.llm_count += int(wo["llm_used"])
            self.template_count += int(not wo["llm_used"])
        return wo

    async def _loop(self):
        try:
            while True:
                batch = await self.consumer.getmany(timeout_ms=1000, max_records=5)
                if not batch:
                    continue
                for _tp, msgs in batch.items():
                    for m in msgs:
                        a = m.value
                        if not isinstance(a, dict) or "asset_id" not in a or a.get("level") not in ("warning", "critical"):
                            logger.warning("알림 형식 오류, 건너뜀: %s", str(a)[:100])
                            continue
                        try:
                            await self.handle_alert(a)
                        except Exception:
                            logger.exception("작업 지시서 생성 실패: %s", a.get("asset_id"))
                await self.consumer.commit()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.error = str(e)
            logger.exception("에이전트 루프 오류")

    def status(self) -> dict:
        return {"running": bool(self.task and not self.task.done()), "started": self.started, "error": self.error,
                "processed_since_start": self.processed, "llm": self.llm_count, "template": self.template_count,
                "model": self.model if self.llm else None, "llm_enabled": self.llm is not None,
                "retrieval_mode": self.retriever.mode if self.retriever else None,
                "manual_sha": self.manual.sha if self.manual else None, "chunks": len(self.manual.chunks) if self.manual else 0}
