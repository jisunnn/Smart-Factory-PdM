"""알림 1건 -> 작업 지시서. 단계: 컨텍스트 -> 매뉴얼 검색 -> (LLM) -> 검증 -> 조립. 어떤 실패도 템플릿 안내로 대체되어 예외를 내지 않는다."""
from __future__ import annotations

import logging

from .context import build_context
from .manual import LEVEL_KO
from .verify import verify_llm_output
from .workorder import compose

logger = logging.getLogger(__name__)


def build_prompt(ctx: dict, plan: dict) -> str:
    facts = "\n".join(f"- {f}" for f in ctx["facts"])
    evidence = "\n".join(f"(E{i}) [{e.ref}] {e.text}" for i, (e, _s) in enumerate(plan["evidence"], 1)) or "(없음)"
    cands = "\n".join(f"- id={c.id} [{c.stage}] {c.text}" for c in plan["steps"])
    return (
        "당신은 설비 예지보전 정비 보조자입니다. 아래 [상황], [매뉴얼 근거], [후보 조치]만 사용하세요.\n"
        "새로운 수치, 시한, 절차를 만들지 마세요. 수치는 [상황]에 적힌 그대로 인용하세요. 한국어로 답하세요.\n\n"
        f"[상황]\n{facts}\n\n[매뉴얼 근거]\n{evidence}\n\n[후보 조치]\n{cands}\n\n"
        "JSON 객체 하나만 출력하세요. 키:\n"
        '- "summary": 현재 상황 요약 2~3문장 (추세와 의심 결함 포함)\n'
        '- "selected_step_ids": 가장 먼저 수행할 후보 조치의 id 1~3개 (위 id 중에서만)\n'
        '- "rationale": 그 조치를 먼저 하는 이유 1~2문장\n')


def run_pipeline(alert: dict, history: list[dict] | None, retriever, llm=None, limiter=None, use_llm: bool = True,
                 model: str | None = None, max_wait: float = 20.0) -> dict:
    ctx = build_context(alert, history)
    plan = retriever.plan(ctx)
    llm_out, reason = None, None
    eligible = ctx["level"] == "critical" or ctx["vib"]             # 호출량 절약: RUL 주의 알림은 템플릿
    if not use_llm or llm is None:
        reason = "LLM 비활성화"
    elif not eligible:
        reason = "RUL 주의 알림은 템플릿 사용"
    elif limiter is not None and not limiter.acquire(max_wait):
        reason = "호출 한도 대기 초과"
    else:
        try:
            out = llm.generate_json(build_prompt(ctx, plan))
            allowed = " ".join(ctx["facts"] + [c.text for c in plan["steps"]] + [e.text for e, _ in plan["evidence"]]
                               + [plan["sla"]] + ["Health Index 60 30 65", LEVEL_KO[ctx["level"]]])
            ok, why = verify_llm_output(out, {c.id for c in plan["steps"]}, allowed, ctx["level"])
            if ok:
                llm_out = out
            else:
                reason = f"검증 실패: {why}"
        except Exception as e:                                       # 네트워크, 한도, JSON 오류 등
            reason = f"LLM 호출 실패: {str(e)[:120]}"
    if reason and llm_out is None:
        logger.info("템플릿 안내 사용 (%s): %s %s", reason, ctx["asset_id"], ctx["level"])
    return compose(ctx, plan, llm_out, reason, model)
