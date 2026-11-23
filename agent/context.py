"""알림 1건의 상황 정보(컨텍스트) 수집. DB 이력을 받아 추세와 수치 근거를 만든다(결정론적, LLM 없음)."""
from __future__ import annotations

import re

from .manual import LEVEL_KO


def _bearing_of(asset_id: str) -> int | None:
    m = re.search(r"(\d+)$", asset_id or "")
    return int(m.group(1)) if m else None


def _parse_label(alert: dict):
    """알림에 구조화된 결함 필드가 없을 때 메시지 끝의 '의심 결함: X' 에서 읽는다."""
    kind, label = alert.get("defect_kind"), alert.get("defect_label")
    if kind and label:
        return kind, label
    m = re.search(r"의심 결함:\s*(.+)$", alert.get("message") or "")
    if not m:
        return "unknown", "판정 불가"
    label = m.group(1).strip()
    kind = "bpfo" if "BPFO" in label and "복합" not in label else "bpfi" if "BPFI" in label and "복합" not in label \
        else "bsf" if "BSF" in label and "복합" not in label else "mixed" if "복합" in label else "unclear"
    return kind, label


def build_context(alert: dict, history: list[dict] | None) -> dict:
    """alert: 알림 dict, history: 시간 오름차순 이력 행(진동: health_index, z_env_*, / RUL: rul_pred, health_index, cycle)."""
    source = alert.get("source") or ""
    vib = source == "ims_set2"
    ctx = {"asset_id": alert["asset_id"], "source": source, "level": alert["level"], "cycle": alert.get("cycle"),
           "ts": alert.get("time"), "hi": alert.get("health_index"), "rul": alert.get("rul_pred"),
           "vib": vib, "defect_kind": None, "defect_label": None, "z": None}
    facts = [f"설비 {ctx['asset_id']}", f"경보 단계 {LEVEL_KO[ctx['level']]}"]
    if ctx["hi"] is not None:
        facts.append(f"Health Index {ctx['hi']:.1f}")
    if ctx["rul"] is not None:
        facts.append(f"예측 RUL {ctx['rul']:.0f} 사이클")
    if vib:
        ctx["defect_kind"], ctx["defect_label"] = _parse_label(alert)
        facts.append(f"의심 결함 {ctx['defect_label']}")
    hist = [h for h in (history or []) if h.get("health_index") is not None]
    trend = "이력 조회 불가"
    if len(hist) >= 2:
        hs = [float(h["health_index"]) for h in hist]
        unit = "파일" if vib else "사이클"
        trend = f"최근 {len(hs)}개 {unit} 동안 Health Index {hs[0]:.1f} → {hs[-1]:.1f} (최저 {min(hs):.1f})"
        if not vib and hist[-1].get("rul_pred") is not None and hist[0].get("rul_pred") is not None:
            trend += f", 예측 RUL {float(hist[0]['rul_pred']):.0f} → {float(hist[-1]['rul_pred']):.0f} 사이클"
        facts.append(trend)
    if vib and hist:
        last = hist[-1]
        z = {k: last.get(f"z_env_{k}") for k in ("bpfo", "bpfi", "bsf")}
        ctx["z"] = z
        if any(v is not None for v in z.values()):
            facts.append("결함 주파수 피크(정상 대비 σ): " + ", ".join(f"{k.upper()} {v:+.1f}" for k, v in z.items() if v is not None))
    ctx["trend"] = trend
    ctx["facts"] = facts
    return ctx
