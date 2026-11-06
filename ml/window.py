"""엔진(설비)별 슬라이딩 윈도우 버퍼 + 학습 때와 동일한 Min-Max 정규화. (torch 불필요)"""
from __future__ import annotations

import time
from collections import defaultdict, deque

import numpy as np


class SlidingWindowScaler:
    def __init__(self, sensor_cols, mins, maxs, window: int, clock=time.monotonic):
        self.sensor_cols = list(sensor_cols)
        # 원본 센서값(예: 2388.06)은 float32로 두면 정밀도 손실이 커서 float64로 계산하고 마지막에만 float32로 변환
        self.mn = np.asarray(mins, dtype=np.float64)
        self.mx = np.asarray(maxs, dtype=np.float64)
        self.window = int(window)
        self._buf: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.window))
        self._last_cycle: dict[str, int] = {}
        self._last_seen: dict[str, float] = {}
        self._clock = clock

    def push(self, asset_id: str, sensors: dict, cycle: int | None = None):
        """사이클 1개를 추가한다. 윈도우가 찼으면 (W, F) 정규화 배열, 아니면 None."""
        self._last_seen[asset_id] = self._clock()
        if cycle is not None:
            last = self._last_cycle.get(asset_id)
            if last is not None:
                if cycle == last:          # Kafka 재전송 등 중복 → 무시
                    return None
                if cycle < last:           # 리플레이 재시작 → 버퍼 초기화
                    self._buf[asset_id].clear()
            self._last_cycle[asset_id] = cycle

        try:
            row = np.array([sensors[c] for c in self.sensor_cols], dtype=np.float64)
        except KeyError as e:
            raise ValueError(f"센서 필드 누락: {e}") from e

        buf = self._buf[asset_id]
        buf.append(row)
        if len(buf) < self.window:
            return None
        arr = np.stack(buf)
        return ((arr - self.mn) / (self.mx - self.mn + 1e-9)).astype(np.float32)

    # ---- 운영용 보조 기능 ----
    def warmup(self) -> dict[str, int]:
        """아직 윈도우가 덜 찬 엔진 → 지금까지 모인 사이클 수."""
        return {a: len(b) for a, b in self._buf.items() if len(b) < self.window}

    def tracked(self) -> int:
        return len(self._last_seen)

    def prune(self, idle_sec: float) -> int:
        """idle_sec 이상 데이터가 없는 엔진의 버퍼를 삭제(메모리 정리). 삭제한 엔진 수 반환."""
        now = self._clock()
        gone = [a for a, t in self._last_seen.items() if now - t > idle_sec]
        for a in gone:
            self._buf.pop(a, None)
            self._last_cycle.pop(a, None)
            self._last_seen.pop(a, None)
        return len(gone)
