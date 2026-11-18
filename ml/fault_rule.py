"""규칙 기반 의심 결함 판정 (2-B). 새 모델 없이, 이미 저장하는 엔벨로프 피크 z-점수만 사용한다.

z = 해당 베어링의 정상 구간 대비 결함 주파수(1차 고조파) 엔벨로프 피크의 표준편차 배수.
  - 가장 큰 z 가 min_z 미만이면 '판정 보류' (결함 주파수가 뚜렷하게 늘지 않음)
  - 1위와 2위 차이가 margin 미만이면 '복합 의심'
  - 그 외에는 1위 결함 주파수에 대응하는 부위를 의심 결함으로 표시
※ min_z, margin 은 검증 전 시작값이다. check_fault_rule.py 결과를 보고 조정한다.
"""
from __future__ import annotations

KINDS = {"bpfo": ("외륜", "BPFO"), "bpfi": ("내륜", "BPFI"), "bsf": ("볼", "BSF")}


def diagnose(z_bpfo, z_bpfi, z_bsf, min_z: float = 3.0, margin: float = 1.5) -> dict:
    zs = {k: float(v) for k, v in (("bpfo", z_bpfo), ("bpfi", z_bpfi), ("bsf", z_bsf)) if v is not None and v == v}
    if not zs:
        return {"kind": "unknown", "label": "판정 불가", "z": None, "second_z": None}
    ranked = sorted(zs.items(), key=lambda kv: -kv[1])
    k1, z1 = ranked[0]
    z2 = ranked[1][1] if len(ranked) > 1 else None
    if z1 < min_z:
        return {"kind": "unclear", "label": "판정 보류 (뚜렷한 증가 없음)", "z": z1, "second_z": z2}
    if z2 is not None and z1 - z2 < margin:
        names = "·".join(KINDS[k][1] for k, z in ranked if z1 - z < margin)
        return {"kind": "mixed", "label": f"복합 의심 ({names})", "z": z1, "second_z": z2}
    part, code = KINDS[k1]
    return {"kind": k1, "label": f"{part}({code})", "z": z1, "second_z": z2}
