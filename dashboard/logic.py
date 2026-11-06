"""대시보드용 데이터 가공 (streamlit/plotly 무관, 단독 테스트 가능)."""
from __future__ import annotations

import numpy as np
import pandas as pd

STATUS_LABEL = {"danger": "위험", "warning": "주의", "ok": "정상", "stale": "끊김"}
LABEL_TO_STATUS = {v: k for k, v in STATUS_LABEL.items()}


def latest_to_df(data: list[dict], warn: float, crit: float, stale_after: float | None = None) -> pd.DataFrame:
    """/api/v1/rul/latest 의 data → 상태(위험/주의/정상/끊김) 컬럼을 붙이고 정렬.
    stale_after(초) 이상 데이터가 없는 엔진은 '끊김'. 정렬: 끊김은 맨 뒤, 나머지는 Health Index 낮은 순."""
    df = pd.DataFrame(data)
    if df.empty:
        return df
    for c in ("health_index", "rul_pred", "rul_true", "age_sec"):
        if c in df:
            df[c] = pd.to_numeric(df[c])          # None → NaN
    df["status"] = np.select(
        [df["health_index"] < crit, df["health_index"] < warn], ["danger", "warning"], default="ok")
    if stale_after is not None and "age_sec" in df:
        df.loc[df["age_sec"] > stale_after, "status"] = "stale"
    df["_stale"] = df["status"] == "stale"
    return df.sort_values(["_stale", "health_index"]).drop(columns="_stale").reset_index(drop=True)


def filter_df(df: pd.DataFrame, statuses: list[str], query: str = "") -> pd.DataFrame:
    """상태 필터와 엔진 이름 검색(부분 일치, 대소문자 무시)."""
    if df.empty:
        return df
    out = df[df["status"].isin(statuses)]
    q = (query or "").strip().lower()
    if q:
        out = out[out["asset_id"].str.lower().str.contains(q, regex=False)]
    return out.reset_index(drop=True)


def format_age(sec) -> str:
    """경과 시간(초) → '45초' / '12분' / '3시간' / '2일'."""
    if sec is None or sec != sec:
        return "-"
    sec = float(sec)
    if sec < 60:
        return f"{sec:.0f}초"
    if sec < 3600:
        return f"{sec / 60:.0f}분"
    if sec < 86400:
        return f"{sec / 3600:.0f}시간"
    return f"{sec / 86400:.0f}일"


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
