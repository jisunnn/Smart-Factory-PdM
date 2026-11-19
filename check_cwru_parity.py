"""서버의 CWRU 분류(특징 추출 + numpy 트리 추론)가 Colab(sklearn) 결과와 일치하는지 확인한다.

사용 (프로젝트 루트, venv 활성화):   python check_cwru_parity.py
필요: ml/artifacts/ 에 cwru_rf.npz, cwru_ref.npz (Colab 내보내기 셀이 만든 파일), CWRU_DATA_DIR 의 3HP .mat 파일
"""
import argparse

import numpy as np

from config.settings import settings
from core.replay_producer import iter_cwru_windows
from ml.cwru_classifier import CWRUClassifier

ap = argparse.ArgumentParser()
ap.add_argument("--ref", default=f"{settings.ML_ARTIFACT_DIR}/cwru_ref.npz")
a = ap.parse_args()

clf = CWRUClassifier.from_file(f"{settings.ML_ARTIFACT_DIR}/cwru_rf.npz")
ref = np.load(a.ref, allow_pickle=False)
index = {(int(f), int(w)): i for i, (f, w) in enumerate(zip(ref["file_id"], ref["win_idx"]))}
print(f"참조 윈도우 {len(index)}개 | classes {clf.classes}")

n = skipped = correct = 0
max_feat = max_proba = 0.0
for msg in iter_cwru_windows(settings.CWRU_DATA_DIR):
    i = index.get((msg["file_id"], msg["win_idx"]))
    if i is None:
        skipped += 1
        continue
    x = clf.features(msg["samples"], msg["rpm"])
    p = clf.predict_proba(x)[0]
    max_feat = max(max_feat, float(np.max(np.abs(x - ref["X"][i]) / (np.abs(ref["X"][i]) + 1e-9))))
    max_proba = max(max_proba, float(np.max(np.abs(p - ref["proba"][i]))))
    correct += clf.classes[int(p.argmax())] == msg["label_true"]
    n += 1

print(f"비교 {n}개 (참조에 없는 윈도우 {skipped}개 제외)")
print(f"특징 최대 상대 오차: {max_feat:.2e} | 확률 최대 차이: {max_proba:.2e} | 정답 일치율: {correct / max(n, 1):.3f}")
ok = n == len(index) and max_feat < 1e-6 and max_proba < 1e-6
print("결과:", "일치 (서버 분류 = Colab 분류)" if ok else "불일치: 비교한 윈도우 수나 오차를 확인하세요.")
