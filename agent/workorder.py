"""작업 지시서 조립. 시한·SOP 코드·조치 문장은 매뉴얼 원문을 그대로 채우고, LLM 출력은 '요약'과 '우선 조치 표시'로만 쓴다."""
from __future__ import annotations

from datetime import datetime

from .manual import LEVEL_KO, STAGE_LABEL

NA_VIB = "해당 없음 (진동 경로)"
NA_RUL = "해당 없음 (RUL 경로)"


def _fmt_time(ts) -> str:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(ts or "-")


def template_summary(ctx: dict) -> str:
    """LLM 없이 만드는 기본 요약 (수치는 모두 알림/이력에서 온 값)."""
    level = LEVEL_KO[ctx["level"]]
    if ctx["vib"]:
        return (f"{ctx['asset_id']}의 진동 Health Index가 {ctx['hi']:.1f}로 {level} 단계입니다. {ctx['trend']}. "
                f"의심 결함은 {ctx['defect_label']}입니다.")
    return (f"{ctx['asset_id']}의 예측 잔여수명이 {ctx['rul']:.0f} 사이클(Health Index {ctx['hi']:.1f})로 {level} 단계입니다. {ctx['trend']}.")


def compose(ctx: dict, plan: dict, llm_out: dict | None, fallback_reason: str | None, model: str | None) -> dict:
    steps = []
    priority = set((llm_out or {}).get("selected_step_ids", []))
    for c in plan["steps"]:
        steps.append({"id": c.id, "stage": STAGE_LABEL.get(c.stage, c.stage) if c.stage != "common" else (c.tag or "공통"),
                      "text": c.text, "ref": c.ref, "priority": c.id in priority})
    loto = [{"id": c.id, "text": c.text, "ref": c.ref} for c in plan["loto"]]
    evidence = [{"id": c.id, "text": c.text, "ref": c.ref, "score": None if s is None else round(float(s), 3)} for c, s in plan["evidence"]]
    summary = (llm_out or {}).get("summary") or template_summary(ctx)
    vib = ctx["vib"]
    equipment = "구름 베어링" if vib else "터보팬 엔진"
    pipeline = "진동 HI·결함분류" if vib else "다중센서 RUL"
    rul_txt = NA_VIB if vib else f"{ctx['rul']:.0f} Cycles"
    defect = ctx["defect_label"] if vib else NA_RUL
    safety = ("LOTO 절차 준수 필수 / 정비 후 HI 65 이상 회복 확인" if vib
              else "가동 중단 및 Overhaul 절차 준수")
    wo = {
        "issued_at": _fmt_time(ctx["ts"]), "alert_time": ctx["ts"], "asset_id": ctx["asset_id"], "source": ctx["source"], "level": ctx["level"],
        "cycle": ctx["cycle"], "equipment": equipment, "pipeline": pipeline, "hi": ctx["hi"], "rul_text": rul_txt,
        "defect": defect, "sop_code": plan["sop"], "sla": plan["sla"], "steps": steps, "loto": loto, "evidence": evidence,
        "safety": safety, "summary": summary, "rationale": (llm_out or {}).get("rationale", ""),
        "llm_used": llm_out is not None, "fallback_reason": fallback_reason, "model": model if llm_out else None,
        "retrieval_mode": plan.get("mode"),
    }
    wo["text"] = render(wo)
    return wo


def render(wo: dict) -> str:
    L = [f"### 작업 지시서 (Work Order)",
         f"- **발행 일시 / 설비 ID:** {wo['issued_at']} / {wo['asset_id']}",
         f"- **설비 분류 및 분석 경로:** {wo['equipment']} ({wo['pipeline']})",
         f"- **현재 상태:** {LEVEL_KO[wo['level']]} (Health Index: {wo['hi']:.1f} / 예측 RUL: {wo['rul_text']})",
         f"- **감지된 결함 유형:** {wo['defect']} (참조 코드: {wo['sop_code']})",
         f"- **필수 대응 시한:** {wo['sla']}",
         "- **조치 권고사항 (매뉴얼 원문):**"]
    for i, s in enumerate(wo["steps"], 1):
        star = " ⭐우선" if s["priority"] else ""
        L.append(f"  {i}. [{s['stage']}] {s['text']}{star}  \n     _(출처: {s['ref']})_")
    if wo["loto"]:
        L.append("- **안전 작업 절차 (LOTO, 매뉴얼 4장):**")
        for i, s in enumerate(wo["loto"], 1):
            L.append(f"  {i}. {s['text']}")
    L.append(f"- **필수 안전 및 검증 기준:** {wo['safety']}")
    tag = "AI 생성" if wo["llm_used"] else "템플릿"
    L.append(f"- **상황 요약 ({tag}):** {wo['summary']}")
    if wo["rationale"]:
        L.append(f"- **우선 조치 이유 (AI):** {wo['rationale']}")
    if wo["evidence"]:
        L.append("- **근거 문단:** " + " / ".join(f"{e['ref']}" for e in wo["evidence"]))
    L.append(f"\n_이 지시서의 시한·SOP 코드·조치 문장은 매뉴얼 원문이며, AI는 요약과 우선 조치 표시만 담당합니다. "
             f"(생성 방식: {tag}{'' if wo['llm_used'] or not wo['fallback_reason'] else ', 사유: ' + wo['fallback_reason']})_")
    return "\n".join(L)
