"""CWRU 결함 유형 분류기 (Random Forest, numpy 추론).
Colab 에서 내보낸 cwru_rf.npz(트리 구조 배열)를 읽어 scikit-learn 없이 확률을 계산한다.
IMS 신호에는 쓰지 않는다(베어링 모델·샘플링 주파수가 다름)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .vib_features import bearing_freqs, extract_all


class CWRUClassifier:
    def __init__(self, arrays: dict):
        self.left, self.right = arrays["left"], arrays["right"]
        self.feature, self.threshold = arrays["feature"], arrays["threshold"]
        self.proba, self.roots = arrays["proba"], arrays["roots"]
        self.classes = [str(c) for c in arrays["classes"]]
        self.meta = json.loads(str(arrays["meta_json"]))
        self.cols = list(self.meta["feature_cols"])
        self.fs = int(self.meta["fs"])
        self.band = tuple(self.meta["env_band"])
        self.window = int(self.meta["window"])
        b = self.meta["bearing"]
        self._b = (b["n"], b["d"], b["D"], b["theta_deg"])

    @classmethod
    def from_file(cls, path):
        with np.load(Path(path), allow_pickle=False) as z:
            return cls({k: z[k] for k in z.files})

    def predict_proba(self, X) -> np.ndarray:
        """특징 행렬(n x 특징수) → 클래스 확률(n x 클래스수). sklearn 과 같이 입력을 float32 로 바꿔 비교한다."""
        X32 = np.atleast_2d(np.asarray(X, dtype=np.float32))
        out = np.zeros((len(X32), self.proba.shape[1]))
        for i, x in enumerate(X32):
            node = self.roots.copy()
            while True:
                leaf = self.left[node] == -1
                if leaf.all():
                    break
                go_left = x[self.feature[node]] <= self.threshold[node]
                nxt = np.where(go_left, self.left[node], self.right[node])
                node = np.where(leaf, node, nxt)
            out[i] = self.proba[node].mean(axis=0)
        return out

    def features(self, samples, rpm: float) -> np.ndarray:
        shaft = float(rpm) / 60.0                         # 파일마다 실제 회전수로 결함 주파수 계산 (노트북과 동일)
        fault = bearing_freqs(shaft, *self._b)
        f = extract_all(np.asarray(samples, dtype=float), self.fs, shaft, fault, self.band)
        return np.array([f[c] for c in self.cols], dtype=np.float64)

    def process(self, samples, rpm: float) -> dict:
        p = self.predict_proba(self.features(samples, rpm))[0]
        return {"proba": p.tolist(), "label": self.classes[int(p.argmax())]}
