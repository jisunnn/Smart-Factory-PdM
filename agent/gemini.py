"""Gemini REST 클라이언트(생성 + 임베딩)와 호출 제한기. 추가 라이브러리 없이 requests 만 쓴다.

주의: 실제 호출은 API 키와 네트워크가 필요하다. check_agent.py 로 먼저 확인하세요.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque

import numpy as np
import requests

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class RateLimiter:
    """분당 호출 수 제한 (슬라이딩 윈도우). acquire() 는 최대 max_wait 초까지 기다린 뒤 실패하면 False."""

    def __init__(self, max_rpm: int):
        self.max_rpm = max(int(max_rpm), 1)
        self._t = deque()
        self._lock = threading.Lock()

    def acquire(self, max_wait: float = 20.0) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                while self._t and now - self._t[0] > 60:
                    self._t.popleft()
                if len(self._t) < self.max_rpm:
                    self._t.append(now)
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)          # 코드 펜스 제거
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)                     # 앞뒤에 설명이 붙은 경우
        if not m:
            raise GeminiError("응답에서 JSON을 찾지 못했습니다.")
        return json.loads(m.group(0))


class GeminiClient:
    def __init__(self, api_key: str, model: str, embed_model: str, timeout: float = 30.0):
        if not api_key:
            raise GeminiError("GEMINI_API_KEY 가 설정되지 않았습니다.")
        self.key, self.model, self.embed_model, self.timeout = api_key, model, embed_model, timeout

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = requests.post(f"{BASE}/{path}", json=body, timeout=self.timeout,
                              headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
        except requests.RequestException as e:
            raise GeminiError(f"네트워크 오류: {e}") from e
        if r.status_code != 200:
            raise GeminiError(f"HTTP {r.status_code}: {r.text[:200]}", r.status_code)
        return r.json()

    def generate_json(self, prompt: str, temperature: float = 0.2) -> dict:
        data = self._post(f"models/{self.model}:generateContent", {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
        })
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as e:
            raise GeminiError(f"응답 형식 오류(차단되었거나 비어 있음): {str(data)[:200]}") from e
        return _extract_json(text)

    def embed(self, texts: list[str]) -> np.ndarray:
        """텍스트 목록 -> (n x d) L2 정규화 임베딩. 한 요청에 최대 50개씩 보낸다."""
        out = []
        for i in range(0, len(texts), 50):
            batch = texts[i:i + 50]
            data = self._post(f"models/{self.embed_model}:batchEmbedContents", {"requests": [
                {"model": f"models/{self.embed_model}", "content": {"parts": [{"text": t}]}} for t in batch]})
            try:
                out += [e["values"] for e in data["embeddings"]]
            except (KeyError, TypeError) as e:
                raise GeminiError(f"임베딩 응답 형식 오류: {str(data)[:200]}") from e
        if len(out) != len(texts):
            raise GeminiError("임베딩 개수가 요청과 다릅니다.")
        v = np.asarray(out, dtype=np.float64)
        return v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-12)
