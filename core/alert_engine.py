"""Health Index 기반 알림 판정 (단계가 '올라가는 순간'에만 알림을 만든다).

단계: 0 정상 / 1 주의(HI < warn) / 2 긴급(HI < crit)
히스테리시스: 한 번 올라간 단계는 임계값 + hysteresis 이상으로 회복해야 내려간다
              (경계에서 HI가 흔들려도 알림이 반복되지 않도록).
"""
from __future__ import annotations

from datetime import datetime, timezone

LEVEL_NAME = {1: "warning", 2: "critical"}


class AlertEngine:
    def __init__(self, warn: float = 60.0, crit: float = 30.0, hysteresis: float = 5.0):
        self.warn, self.crit, self.h = float(warn), float(crit), float(hysteresis)
        self._level: dict[str, int] = {}
        self._last_cycle: dict[str, int] = {}

    def reset(self):
        self._level.clear()
        self._last_cycle.clear()

    def level_of(self, asset_id: str) -> int:
        return self._level.get(asset_id, 0)

    def _next_level(self, cur: int, hi: float) -> int:
        lvl = cur
        if hi < self.crit:                       # 상승
            lvl = 2
        elif hi < self.warn and cur < 1:
            lvl = 1
        if lvl == 2 and hi >= self.crit + self.h:        # 하강 (히스테리시스 적용)
            lvl = 1 if hi < self.warn + self.h else 0
        if lvl == 1 and hi >= self.warn + self.h:
            lvl = 0
        return lvl

    def evaluate(self, asset_id: str, cycle, health_index: float, rul_pred: float, ts: str | None = None):
        """예측 1건 평가 → 단계가 올라갔으면 알림 dict, 아니면 None."""
        if cycle is not None:
            last = self._last_cycle.get(asset_id)
            if last is not None and cycle < last:        # 리플레이 재시작 → 상태 초기화
                self._level[asset_id] = 0
            self._last_cycle[asset_id] = cycle

        cur = self._level.get(asset_id, 0)
        new = self._next_level(cur, float(health_index))
        self._level[asset_id] = new
        if new <= cur:
            return None

        if new == 2:
            msg = (f"긴급: {asset_id} Health Index {health_index:.1f} (기준 {self.crit:g} 미만), "
                   f"예상 잔여 약 {rul_pred:.0f}사이클 — 즉시 점검 필요")
        else:
            msg = (f"주의: {asset_id} Health Index {health_index:.1f} (기준 {self.warn:g} 미만), "
                   f"예상 잔여 약 {rul_pred:.0f}사이클 — 점검 계획 필요")
        return {
            "time": ts or datetime.now(timezone.utc).isoformat(),
            "asset_id": asset_id,
            "level": LEVEL_NAME[new],
            "cycle": cycle,
            "health_index": float(health_index),
            "rul_pred": float(rul_pred),
            "message": msg,
        }
