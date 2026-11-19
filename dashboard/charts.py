"""Plotly 차트 생성 함수."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLOR = {"danger": "#d62728", "warning": "#ff9f1c", "ok": "#2ca02c", "stale": "#9e9e9e"}


def fleet_bar(df, warn, crit, total=None):
    """전체 엔진 Health Index 막대 (낮은 순)."""
    fig = go.Figure(go.Bar(
        x=df["asset_id"], y=df["health_index"],
        marker_color=[COLOR[s] for s in df["status"]],
        customdata=df[["cycle", "rul_pred"]].values,
        hovertemplate="%{x}<br>Health Index %{y:.1f}<br>cycle %{customdata[0]}<br>RUL %{customdata[1]:.0f}<extra></extra>",
    ))
    fig.add_hline(y=warn, line_dash="dot", line_color=COLOR["warning"])
    fig.add_hline(y=crit, line_dash="dot", line_color=COLOR["danger"])
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
        yaxis=dict(range=[0, 100], title="Health Index"),
        xaxis=dict(categoryorder="array", categoryarray=df["asset_id"].tolist(),
                   showticklabels=False, title=(f"표시 {len(df)}대" + (f" / 전체 {total}대" if total and total > len(df) else "") + " (Health Index 낮은 순, 회색=데이터 끊김)")),
    )
    return fig


def gauge(hi, warn, crit):
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=float(hi), number={"valueformat": ".1f"},
        title={"text": "Health Index"},
        gauge={"axis": {"range": [0, 100]}, "bar": {"color": "#4c78a8"},
               "steps": [{"range": [0, crit], "color": "#f4b6b6"},
                         {"range": [crit, warn], "color": "#ffd9a0"},
                         {"range": [warn, 100], "color": "#bfe5bf"}]},
    ))
    fig.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=10))
    return fig


def history_fig(hdf, asset_id, warn=None, crit=None):
    """선택한 엔진의 RUL 예측 대 실제(위) + Health Index(아래)."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.08)
    fig.add_trace(go.Scatter(x=hdf["cycle"], y=hdf["rul_pred"], name="예측 RUL", line=dict(color="#4c78a8")), row=1, col=1)
    if "rul_true" in hdf and hdf["rul_true"].notna().any():
        fig.add_trace(go.Scatter(x=hdf["cycle"], y=hdf["rul_true"], name="실제 RUL (데모용 정답)",
                                 line=dict(color="#888", dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=hdf["cycle"], y=hdf["health_index"], name="Health Index",
                             line=dict(color="#2ca02c")), row=2, col=1)
    if warn is not None:
        fig.add_hline(y=warn, line_dash="dot", line_color=COLOR["warning"], row=2, col=1)
    if crit is not None:
        fig.add_hline(y=crit, line_dash="dot", line_color=COLOR["danger"], row=2, col=1)
    fig.update_yaxes(title_text="RUL (cycles)", row=1, col=1)
    fig.update_yaxes(title_text="Health Index", range=[0, 100], row=2, col=1)
    fig.update_xaxes(title_text="cycle", row=2, col=1)
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10), title=f"{asset_id}",
                      legend=dict(orientation="h", y=1.08))
    return fig


def vib_hi_fig(series, warn, crit):
    """베어링별 Health Index 추이. series: {베어링번호: DataFrame(elapsed_h, health_index)}"""
    fig = go.Figure()
    for b, df in series.items():
        fig.add_trace(go.Scatter(x=df["elapsed_h"], y=df["health_index"], mode="lines", name=f"베어링 {b}"))
    fig.add_hline(y=warn, line_dash="dot", line_color=COLOR["warning"])
    fig.add_hline(y=crit, line_dash="dot", line_color=COLOR["danger"])
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.1),
                      yaxis=dict(range=[0, 105], title="Health Index"), xaxis=dict(title="elapsed (h)"))
    return fig


def spectrum_fig(freqs, cur, cur_label, ref=None, ref_label="", fault_hz=None):
    """엔벨로프 스펙트럼 (현재 + 비교용 이전 스펙트럼) 과 결함 주파수 표시선."""
    fig = go.Figure()
    if ref is not None:
        fig.add_trace(go.Scatter(x=freqs, y=ref, mode="lines", name=ref_label, line=dict(color="#9e9e9e", width=1)))
    fig.add_trace(go.Scatter(x=freqs, y=cur, mode="lines", name=cur_label, line=dict(color=COLOR["danger"], width=2)))
    for name, f0 in (fault_hz or {}).items():
        if f0 <= float(freqs[-1]):
            fig.add_vline(x=f0, line_dash="dot", line_color="#4c78a8", annotation_text=name, annotation_position="top")
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.12),
                      xaxis=dict(title="주파수 (Hz)"), yaxis=dict(title="엔벨로프 진폭"))
    return fig


def spectrogram_fig(xs, freqs, Z, fault_hz=None):
    """시간 x 주파수 스펙트로그램 (진폭은 log10 색상)."""
    import numpy as np
    fig = go.Figure(go.Heatmap(x=xs, y=freqs, z=np.log10(np.maximum(Z, 1e-6)), colorscale="Viridis",
                               colorbar=dict(title="log10 진폭")))
    for name, f0 in (fault_hz or {}).items():
        if f0 <= float(freqs[-1]):
            fig.add_hline(y=f0, line_dash="dot", line_color="white", annotation_text=name, annotation_position="right")
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), xaxis=dict(title="elapsed (h)"),
                      yaxis=dict(title="주파수 (Hz)"))
    return fig


def proba_fig(classes, proba, labels=None):
    """클래스별 확률 막대 (가장 높은 클래스를 강조)."""
    labels = labels or {}
    names = [labels.get(c, c) for c in classes]
    top = max(range(len(proba)), key=lambda i: proba[i])
    fig = go.Figure(go.Bar(x=[100 * p for p in proba], y=names, orientation="h",
                           marker_color=[COLOR["danger"] if i == top else "#9e9e9e" for i in range(len(proba))],
                           text=[f"{100 * p:.0f}%" for p in proba], textposition="auto"))
    fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10), xaxis=dict(range=[0, 100], title="확률 (%) - 마지막 윈도우"),
                      showlegend=False)
    return fig
