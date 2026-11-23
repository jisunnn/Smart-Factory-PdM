"""LLM 출력 검증. 하나라도 어긋나면 템플릿 안내로 대체한다."""
from __future__ import annotations

import re

NUM = re.compile(r"\d+(?:\.\d+)?")
BANNED_CRITICAL = ("계속 가동", "운전을 지속", "점검 불필요", "무시해도", "문제없", "정상 운전 유지")


def numbers_in(text: str) -> list[float]:
    return [float(x) for x in NUM.findall(text or "")]


def _allowed(n: float, allowed: list[float]) -> bool:
    if float(n).is_integer() and n <= 3:                      # '2개', '1차' 같은 작은 수는 허용
        return True
    return any(abs(a - n) <= 0.51 for a in allowed)           # 반올림 인용(27.1 -> 27)은 허용


def verify_llm_output(out, candidate_ids: set[str], allowed_text: str, level: str) -> tuple[bool, str]:
    if not isinstance(out, dict):
        return False, "출력이 JSON 객체가 아님"
    summary, rationale, ids = out.get("summary"), out.get("rationale", ""), out.get("selected_step_ids")
    if not isinstance(summary, str) or not (10 <= len(summary) <= 500):
        return False, "summary 형식/길이 오류"
    if not isinstance(rationale, str) or len(rationale) > 400:
        return False, "rationale 형식/길이 오류"
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return False, "selected_step_ids 형식 오류"
    if candidate_ids and not (1 <= len(ids) <= 3):
        return False, "selected_step_ids 개수 오류"
    if not set(ids) <= candidate_ids:
        return False, f"후보에 없는 조치 id: {sorted(set(ids) - candidate_ids)}"
    allowed = numbers_in(allowed_text)
    for field, t in (("summary", summary), ("rationale", rationale)):
        for n in numbers_in(t):
            if not _allowed(n, allowed):
                return False, f"{field}에 근거 없는 수치: {n:g}"
    if level == "critical" and any(b in summary + rationale for b in BANNED_CRITICAL):
        return False, "긴급 단계에 부적절한 표현"
    return True, "ok"
