"""의심 결함 규칙(2-B)이 IMS Set 2 전체에서 어떻게 판정하는지 확인한다.

사용 (프로젝트 루트, venv 활성화):   python check_fault_rule.py
IMS 원본 984파일을 서버와 같은 코드로 처리한다(약 1~2분). 결과를 보고 min_z, margin 을 조정할 수 있다.
기대: 고장 나는 베어링 1은 주의 기준 아래에서 '외륜(BPFO)'이 가장 많아야 한다(IMS Set 2의 알려진 고장 유형).
"""
from collections import Counter
from pathlib import Path

import pandas as pd

from config.settings import settings
from ml.fault_rule import diagnose
from ml.ims_scorer import IMSScorer

warn = getattr(settings, "ALERT_WARN_HI", 60.0)
crit = getattr(settings, "ALERT_CRIT_HI", 30.0)
min_z, margin = getattr(settings, "FAULT_MIN_Z", 3.0), getattr(settings, "FAULT_MARGIN", 1.5)
files = sorted(p for p in Path(settings.IMS_DATA_DIR).iterdir() if p.is_file())
scorer = IMSScorer.from_files(settings.ML_ARTIFACT_DIR)
print(f"파일 {len(files)}개 처리 중... (min_z={min_z}, margin={margin})")

rows = []
for i, p in enumerate(files):
    arr = pd.read_csv(p, sep="\t", header=None).values
    for b in range(1, 5):
        r = scorer.process(b, arr[:, b - 1].tolist(), with_spectrum=False)
        d = diagnose(r["z_env_bpfo"], r["z_env_bpfi"], r["z_env_bsf"], min_z, margin)
        rows.append({"file_idx": i, "bearing": b, "hi": r["health_index"], "kind": d["kind"], "label": d["label"],
                     "z_bpfo": r["z_env_bpfo"], "z_bpfi": r["z_env_bpfi"], "z_bsf": r["z_env_bsf"]})
df = pd.DataFrame(rows)

for b in range(1, 5):
    s = df[df["bearing"] == b]
    low = s[s["hi"] < warn]
    print(f"\n=== 베어링 {b} ===  Health Index < {warn:g}: {len(low)}개 파일 ({len(low) / len(s):.0%})")
    if low.empty:
        print("  주의 기준 아래로 내려간 적이 없습니다.")
        continue
    first = low.iloc[0]
    print(f"  첫 주의: 파일 {int(first['file_idx'])} (약 {first['file_idx'] / 6:.0f}시간) → {first['label']}")
    print("  주의 이하 구간의 판정 분포:", dict(Counter(low["label"])))
    crit_low = s[s["hi"] < crit]
    if len(crit_low):
        print("  긴급 이하 구간의 판정 분포:", dict(Counter(crit_low["label"])))
    tail = s.tail(50)
    print(f"  마지막 50개 파일의 평균 z  BPFO {tail['z_bpfo'].mean():+.1f} / BPFI {tail['z_bpfi'].mean():+.1f} / BSF {tail['z_bsf'].mean():+.1f}")

df.to_csv("fault_rule_check.csv", index=False)
print("\n상세 결과: fault_rule_check.csv")
