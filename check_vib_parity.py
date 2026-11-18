"""서버(진동 점수 계산)가 Colab 결과와 일치하는지 확인한다.

사용 (프로젝트 루트, venv 활성화):
  python check_vib_parity.py --parquet C:/경로/ims_scores_4ch.parquet
Colab 노트북이 저장한 ims_scores_4ch.parquet 의 Autoencoder 열과, IMS 원본 파일에서 서버 코드로 다시 계산한 값을 비교한다.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import settings
from ml.ims_scorer import IMSScorer

ap = argparse.ArgumentParser()
ap.add_argument("--parquet", required=True)
ap.add_argument("--first", type=int, default=30, help="앞에서부터 연속으로 확인할 파일 수(평활 비교용)")
a = ap.parse_args()

df = pd.read_parquet(a.parquet)
files = sorted(p for p in Path(settings.IMS_DATA_DIR).iterdir() if p.is_file())
scorer = IMSScorer.from_files(settings.ML_ARTIFACT_DIR)
print(f"파일 {len(files)}개 | 비교 대상 parquet {len(df)}행")

rows = []
spot = sorted(set([min(i, len(files) - 1) for i in (300, 600, 700, 800, len(files) - 1)]))   # 열화 구간 샘플
for phase, idxs, smooth in (("연속(평활 포함)", range(min(a.first, len(files))), True), ("열화 구간 샘플", spot, False)):
    scorer.reset()
    for i in idxs:
        arr = pd.read_csv(files[i], sep="\t", header=None).values
        for b in range(1, 5):
            r = scorer.process(b, arr[:, b - 1].tolist(), with_spectrum=False)
            ref = df[(df["file_idx"] == i) & (df["bearing"] == b)].iloc[0]
            rows.append({"구간": phase, "file_idx": i, "bearing": b,
                         "ratio_diff": abs(r["ratio"] - ref["Autoencoder|ratio"]) / max(ref["Autoencoder|ratio"], 1e-12),
                         "hi_diff": abs(r["health_raw"] - ref["Autoencoder|hi"]),
                         "hi_s_diff": abs(r["health_index"] - ref["Autoencoder|hi_s"]) if smooth else np.nan})

res = pd.DataFrame(rows)
print(res.groupby("구간")[["ratio_diff", "hi_diff", "hi_s_diff"]].max().round(6))
ok = (res["ratio_diff"].max() < 1e-3) and (res["hi_diff"].max() < 0.05)
print("\n결과:", "일치 (서버 점수 = Colab 점수)" if ok else "불일치: 특징/변환/가중치 중 하나가 다릅니다. 위 표에서 오차가 큰 구간을 알려 주세요.")
