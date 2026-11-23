"""매뉴얼 검색(RAG). 구조 규칙으로 SOP 절을 먼저 좁히고, 결함 유형이 불명확할 때만 임베딩 유사도(numpy)로 근거 문단을 고른다.

임베딩은 매뉴얼 조각과 (경보 단계 x 결함 유형 같은 유한한) 질의 문장에 대해 한 번 계산해 JSON 캐시에 저장한다.
Gemini 임베딩이 실패하면 API 없이 동작하는 키워드 임베딩으로 대체한다(검색은 항상 동작).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time

import numpy as np

from .manual import Manual

logger = logging.getLogger(__name__)


class KeywordEmbedder:
    """API 없이 쓰는 대체 임베딩: 글자 2-3gram 해시 주머니 -> L2 정규화."""
    name = "keyword-hash"
    DIM = 1024

    def embed(self, texts):
        out = np.zeros((len(texts), self.DIM))
        for i, t in enumerate(texts):
            t = re.sub(r"\s+", "", t.lower())
            for n in (2, 3):
                for j in range(len(t) - n + 1):
                    out[i, int(hashlib.md5(t[j:j + n].encode()).hexdigest(), 16) % self.DIM] += 1.0
        return out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-12)


class Retriever:
    def __init__(self, manual: Manual, embedder=None, model_name: str = "none", cache_path: str | None = None):
        self.manual = manual
        self.primary = embedder                     # GeminiClient(embed 메서드) 또는 None
        self.model_name = model_name
        self.fallback = KeywordEmbedder()
        self.cache_path = cache_path
        self.mode = "keyword" if embedder is None else "gemini"
        self._cache: dict[str, list] = {}
        self._retry_after = 0.0
        self._load_cache()

    # ---- 캐시 ----
    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.model_name}|{text}".encode()).hexdigest()[:20]

    def _load_cache(self):
        if self.cache_path and os.path.exists(self.cache_path):
            try:
                self._cache = json.load(open(self.cache_path, encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self):
        if self.cache_path:
            try:
                json.dump(self._cache, open(self.cache_path, "w", encoding="utf-8"))
            except OSError as e:
                logger.warning("임베딩 캐시 저장 실패: %s", e)

    # ---- 임베딩 ----
    def embed(self, texts: list[str]) -> np.ndarray:
        """텍스트 -> 벡터. 같은 벡터 공간 안에서만 비교해야 하므로, 한 번의 계산에는 한 가지 임베더만 쓴다."""
        if self.primary is not None and time.monotonic() >= self._retry_after:
            try:
                need = [t for t in texts if self._key(t) not in self._cache]
                if need:
                    vecs = self.primary.embed(need)
                    for t, v in zip(need, vecs):
                        self._cache[self._key(t)] = np.round(v, 6).tolist()
                    self._save_cache()
                self.mode = "gemini"
                return np.asarray([self._cache[self._key(t)] for t in texts])
            except Exception as e:                      # 네트워크/키/모델 오류 -> 5분간 API 시도를 멈추고 대체 임베딩 사용
                logger.warning("Gemini 임베딩 실패, 키워드 검색으로 대체합니다: %s", e)
                self._retry_after = time.monotonic() + 300
        self.mode = "keyword"
        return self.fallback.embed(texts)

    def warm_up(self):
        """시작 시 매뉴얼 조각과 알려진 질의를 미리 임베딩해 캐시한다."""
        from itertools import permutations
        texts = [c.text for c in self.manual.chunks if c.kind in ("feature", "cause")]
        texts += [self.query_text(k) for k in ("mixed", "unclear", "unknown")]
        for a, b in permutations(("bpfo", "bpfi", "bsf"), 2):          # 질의 문장의 모든 경우를 미리 계산 (실행 중 임베딩 호출 방지)
            texts.append(self.query_text("mixed", {a: 9, b: 8}))
        self.embed(texts)

    # ---- 검색 ----
    @staticmethod
    def query_text(kind: str, z: dict | None = None) -> str:
        names = {"bpfo": "외륜 결함 주파수 BPFO", "bpfi": "내륜 결함 주파수 BPFI", "bsf": "전동체 볼 결함 주파수 BSF"}
        if kind == "mixed" and z:
            top = [names[k] for k, v in sorted(z.items(), key=lambda kv: -(kv[1] or 0)) if v is not None and v >= 3 and k in names]
            if len(top) >= 2:
                return " 와 ".join(top[:2]) + " 가 동시에 상승하는 복합 결함"
        if kind == "mixed":
            return "2개 이상의 결함 주파수가 동시에 상승하는 복합 결함"
        return "결함 주파수 피크가 뚜렷하지 않은 이상 진동 센서 신호 이상"

    def _best_other(self, sop: str, query: str):
        pool = [c for c in self.manual.chunks if c.kind == "feature" and c.sop != sop]
        if not pool:
            return None
        vecs = self.embed([query] + [c.text for c in pool])      # 질의와 조각을 같은 공간에서 비교
        sims = vecs[1:] @ vecs[0]
        i = int(np.argmax(sims))
        return pool[i], float(sims[i])

    def plan(self, ctx: dict) -> dict:
        """경보 종류 + 단계 + 결함 유형 -> 조치 후보, 대응 시한, 근거 문단."""
        m, level, kind = self.manual, ctx["level"], ctx.get("defect_kind")
        if ctx["source"] != "ims_set2":                                 # RUL 경로
            steps = [c for c in m.chunks if c.sop == "SOP-RUL" and c.stage == level]
            return {"sop": "SOP-RUL", "sla": m.rul_sla[level], "steps": steps, "evidence": [], "loto": [], "mode": self.mode}
        sop = m.sop_for_kind(kind)
        evidence = [(c, None) for c in m.chunks if c.sop == sop and c.kind in ("feature", "cause")]
        if kind in ("mixed", "unclear", "unknown"):                    # 모호할 때만 유사도로 다른 절의 근거를 하나 더 찾는다
            best = self._best_other(sop, self.query_text(kind, ctx.get("z")))
            if best:
                evidence.append(best)
        return {"sop": sop, "sla": m.sla[level], "steps": m.steps_for(sop, level), "evidence": evidence,
                "loto": list(m.loto) if level == "critical" else [], "mode": self.mode}
