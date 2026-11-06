"""C-MAPSS FD001 RUL 추론기 (CPU). 노트북(pdm_rul_model.ipynb)의 모델 구조와 동일해야 합니다."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from .window import SlidingWindowScaler


class CNNBiLSTM(nn.Module):
    def __init__(self, n_feat, conv=64, hid=64, drop=0.3):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(n_feat, 32, 3, padding=1), nn.ReLU(),
            nn.Conv1d(32, conv, 3, padding=1), nn.ReLU())
        self.lstm = nn.LSTM(conv, hid, batch_first=True, bidirectional=True)
        self.head = nn.Sequential(nn.Dropout(drop), nn.Linear(hid * 2, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, x):                                   # x: (B, T, F)
        h = self.conv(x.transpose(1, 2)).transpose(1, 2)
        _, (hn, _) = self.lstm(h)
        feat = torch.cat([hn[-2], hn[-1]], dim=1)
        return self.head(feat).squeeze(-1)


class RULPredictor:
    def __init__(self, artifact_dir, model_files=None, meta_file="rul_meta_fd001.json"):
        d = Path(artifact_dir)
        meta = json.loads((d / meta_file).read_text(encoding="utf-8"))
        cols = meta["sensor_cols"]
        self.cap = float(meta["rul_cap"])
        self.scaler = SlidingWindowScaler(
            cols, [meta["min"][c] for c in cols], [meta["max"][c] for c in cols], meta["window"])

        self.models = []
        for f in (model_files or ["rul_cnn_bilstm_fd001.pt"]):   # 여러 개면 평균(앙상블)
            m = CNNBiLSTM(len(cols))
            m.load_state_dict(torch.load(d / f, map_location="cpu"))
            m.eval()
            self.models.append(m)
        torch.set_num_threads(1)

    def update(self, asset_id: str, sensors: dict, cycle: int | None = None):
        """사이클 1개 입력 → 윈도우가 차기 전이면 None, 차면 {'rul', 'health_index'}."""
        w = self.scaler.push(asset_id, sensors, cycle)
        if w is None:
            return None
        x = torch.from_numpy(w[None])
        with torch.no_grad():
            pred = float(np.mean([m(x).item() for m in self.models]))
        rul = float(np.clip(pred * self.cap, 0.0, self.cap))
        return {"rul": rul, "health_index": rul / self.cap * 100.0}

    def warmup(self) -> dict:
        return self.scaler.warmup()

    def prune(self, idle_sec: float) -> int:
        return self.scaler.prune(idle_sec)

    def tracked(self) -> int:
        return self.scaler.tracked()
