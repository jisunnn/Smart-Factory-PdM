"""IMS 베어링 이상 점수 / Health Index 계산기 (Autoencoder, numpy 추론).

노트북의 처리 순서를 그대로 따른다:
  특징 28개 → 부호 보존 로그 → 베어링별 정상 구간 z-score → 클리핑 → Autoencoder 재구성 오차
  → 임계값 대비 비율 → Health Index(0~100) → 5개 이동 중앙값(평활)
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

from .vib_features import extract_all

LAYERS = (("enc.0", True), ("enc.2", True), ("enc.4", False), ("dec.0", True), ("dec.2", True), ("dec.4", False))
SPEC_MAX_HZ, SPEC_STEP_HZ = 1000, 2        # 저장하는 엔벨로프 스펙트럼: 0~1000Hz, 2Hz 간격(500점)


def load_weights(path):
    """state_dict(.pt) → numpy 배열 딕셔너리. (로드할 때만 torch 필요)"""
    import torch
    sd = torch.load(path, map_location="cpu")
    return {k: v.detach().cpu().numpy().astype(np.float32) for k, v in sd.items()}


def slog(x):
    return np.sign(x) * np.log1p(np.abs(x))


class IMSScorer:
    def __init__(self, meta: dict, weights: dict, smooth: int = 5):
        self.meta = meta
        self.cols = list(meta["feature_cols"])
        self.z_clip = float(meta["transform"]["z_clip"])
        self.base = {int(b): (np.asarray(v["mu"], dtype=np.float64), np.asarray(v["sd"], dtype=np.float64))
                     for b, v in meta["baseline"].items()}
        sig = meta["signal"]
        self.fs, self.shaft, self.band = int(sig["fs"]), float(sig["shaft_hz"]), tuple(sig["env_band"])
        self.fault = {k: float(v) for k, v in sig["fault_hz"].items()}
        self.thr = float(meta["ae"]["threshold"])
        self.r_max = float(meta["hi"]["r_max"])
        self.w = weights
        self._hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=smooth))
        self._idx = {c: i for i, c in enumerate(self.cols)}

    @classmethod
    def from_files(cls, artifact_dir, meta_file="ims_anomaly_meta.json", weights_file="ims_ae.pt"):
        d = Path(artifact_dir)
        meta = json.loads((d / meta_file).read_text(encoding="utf-8"))
        return cls(meta, load_weights(d / weights_file))

    def reset(self):
        self._hist.clear()

    def _ae_score(self, z: np.ndarray) -> float:
        h = z.astype(np.float32)
        for name, relu in LAYERS:
            h = h @ self.w[f"{name}.weight"].T + self.w[f"{name}.bias"]
            if relu:
                h = np.maximum(h, 0.0)
        return float(np.mean((h - z.astype(np.float32)) ** 2))

    def to_hi(self, ratio: float) -> float:
        return float(100.0 * (1.0 - np.clip(np.log10(max(ratio, 1.0)) / np.log10(self.r_max), 0.0, 1.0)))

    def process(self, bearing: int, x, with_spectrum: bool = True) -> dict:
        """파형 1개(1초) → 점수/HI/주요 지표/스펙트럼."""
        if bearing not in self.base:
            raise ValueError(f"기준 통계가 없는 베어링: {bearing}")
        feats, (f, X) = extract_all(x, self.fs, self.shaft, self.fault, self.band, return_env_spec=True)
        vec = np.array([feats[c] for c in self.cols], dtype=np.float64)
        mu, sd = self.base[bearing]
        z = np.clip((slog(vec) - mu) / sd, -self.z_clip, self.z_clip)

        score = self._ae_score(z)
        ratio = max(score / self.thr, 1e-12)
        hi_raw = self.to_hi(ratio)
        hist = self._hist[bearing]
        hist.append(hi_raw)
        out = {
            "bearing": bearing, "ae_score": score, "ratio": ratio, "health_raw": hi_raw,
            "health_index": float(np.median(hist)),
            "rms": float(feats["rms"]), "kurtosis": float(feats["kurtosis"]),
        }
        for key, col in (("z_env_bpfo", "env_BPFO_h1"), ("z_env_bpfi", "env_BPFI_h1"), ("z_env_bsf", "env_BSF_h1")):
            out[key] = float(z[self._idx[col]]) if col in self._idx else None
        if with_spectrum:
            n = SPEC_MAX_HZ // SPEC_STEP_HZ
            res = float(f[1] - f[0])
            k = max(int(round(SPEC_STEP_HZ / res)), 1)
            seg = X[: n * k].reshape(n, k).max(axis=1)           # 구간 최대값으로 다운샘플
            out["spec"] = [round(float(v), 5) for v in seg]
        return out
