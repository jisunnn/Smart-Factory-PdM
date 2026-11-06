"""대시보드용 데이터 가공 (streamlit/plotly 무관, 단독 테스트 가능)."""
from __future__ import annotations

import numpy as np
import pandas as pd

STATUS_LABEL = {"danger": "위험", "warning": "주의", "ok": "정상"}


def latest_to_df(data: list[dict], warn: float, crit: float) -> pd.DataFrame:
    """/api/v1/rul/latest 의 data → 상태(위험/주의/정상) 컬럼을 붙이고 Health Index 낮은 순으로 정렬."""
    df = pd.DataFrame(data)
    if df.empty:
        return df
    for c in ("health_index", "rul_pred", "rul_true"):
        if c in df:
            df[c] = pd.to_numeric(df[c])          # None → NaN (데모 정답이 없는 경우 대비)
    df["status"] = np.select(
        [df["health_index"] < crit, df["health_index"] < warn], ["danger", "warning"], default="ok")
    return df.sort_values("health_index").reset_index(drop=True)


def history_to_df(data: list[dict]) -> pd.DataFrame:
    """/api/v1/rul/history 의 data → 사이클당 마지막 기록 1건만 남기고 사이클 순 정렬.
    (리플레이를 여러 번 돌리면 같은 사이클이 중복되므로)"""
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"])
    if "rul_true" in df:
        df["rul_true"] = pd.to_numeric(df["rul_true"])
    df = df.sort_values("time").drop_duplicates("cycle", keep="last").sort_values("cycle")
    return df.reset_index(drop=True)


def cycles_to_hours(cycles: float, hours_per_cycle: float) -> float:
    return float(cycles) * float(hours_per_cycle)
