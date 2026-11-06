"""Smart Factory PdM 관제 대시보드 (Streamlit + Plotly).

실행:  streamlit run dashboard/app.py      (FastAPI 서버가 먼저 떠 있어야 함)
화면은 FastAPI 의 /api/v1/* 만 호출한다 (DB에 직접 접속하지 않음).
"""
import os

import pandas as pd
import requests
import streamlit as st

from charts import fleet_bar, gauge, history_fig
from logic import (LABEL_TO_STATUS, STATUS_LABEL, cycles_to_hours, filter_df, format_age,
                   history_to_df, latest_to_df)

st.set_page_config(page_title="Smart Factory PdM", page_icon="🏭", layout="wide")
API_DEFAULT = os.getenv("PDM_API_URL", "http://localhost:8000")

#========================================알림 추가=====

# 🚨 카드형 그리드 레이아웃과 개별 확인 기능이 적용된 긴급 알림 모달
@st.dialog("🚨 긴급 설비 점검 알림 (Critical)")
def show_critical_dialog(crit_alerts):
    st.error("즉각적인 조치가 필요한 긴급 설비 이상이 감지되었습니다!")
    st.caption("각 설비 카드의 '확인 완료'를 누르면 해당 알림만 개별 처리됩니다.")
    
    # 2열 또는 3열 그리드로 카드 배치 (여기서는 2열 구조)
    cols = st.columns(2)
    
    for idx, a in enumerate(crit_alerts):
        # 짝수/홀수 인덱스로 좌우 열 번갈아가며 배치
        col = cols[idx % 2]
        
        with col:
            # 📦 네모난 박스 형태의 카드 컨테이너 생성 (Streamlit 기본 테두리 기능)
            with st.container(border=True):
                st.markdown(f"**🔴 {a['asset_id']}**")
                st.caption(f"가동 사이클: {a['cycle']}사이클")
                st.write(f"{a['message']}")
                
                # 개별 알림 확인 버튼 (고유 key 부여 필수)
                if st.button("확인 완료", key=f"ack_btn_{a['id']}"):
                    # 백엔드로 해당 알림 ID만 개별 확인 요청 전송
                    api(base, "POST", f"/api/v1/alerts/{a['id']}/ack")
                    st.toast(f"✅ {a['asset_id']} 알림이 확인 처리되었습니다.", icon="✅")
                    
                    # 팝업 상태를 갱신하여 화면에서 즉시 제거되도록 리프레시
                    st.rerun()

    st.markdown("---")
    
    # 전체 일괄 확인 버튼 (하단 배치)
    if st.button("모든 긴급 알림 일괄 확인", type="primary", use_container_width=True):
        for a in crit_alerts:
            api(base, "POST", f"/api/v1/alerts/{a['id']}/ack")
        if "popup_shown" in st.session_state:
            del st.session_state.popup_shown
        st.rerun()
#========================================알림 추가=====

# ---------------------------------------------------------------- API 호출
def api(base, method, path, **kw):
    try:
        r = requests.request(method, f"{base}{path}", timeout=10, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            return None, f"{r.status_code}: {detail}"
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "서버에 연결할 수 없습니다. FastAPI 서버(uvicorn main:app)가 실행 중인지 확인하세요."
    except Exception as e:
        return None, str(e)


def show_table(df):
    """표 출력. pyarrow 가 깨진 환경에서는 HTML 표로 대체한다 (st.dataframe 은 pyarrow 필요)."""
    try:
        st.dataframe(df, hide_index=True)
    except ImportError:
        st.markdown(df.to_html(index=False, border=0, float_format=lambda v: f"{v:.1f}"), unsafe_allow_html=True)


# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.header("설정")
    base = st.text_input("API 주소", API_DEFAULT).rstrip("/")

    st.subheader("데이터 리플레이")
    st.selectbox("데이터", ["cycle"], format_func=lambda k: "C-MAPSS FD001 (test 엔진)",
                 help="IMS/CWRU 진동 리플레이는 진동 처리 워커 구현 후 추가됩니다.")
    speed = st.select_slider("재생 속도", options=["느림", "보통", "빠름"], value="보통",
                             help="느림 0.2초 / 보통 0.05초 / 빠름 0.005초 (메시지 간격)")
    interval = {"느림": 0.2, "보통": 0.05, "빠름": 0.005}[speed]
    loop = st.checkbox("끝나면 처음부터 반복")

    c1, c2 = st.columns(2)
    if c1.button("▶ 시작"):
        res, err = api(base, "POST", "/api/v1/replay/start", json={"kind": "cycle", "interval": interval, "loop": loop})
        st.error(err) if err else st.success("리플레이를 시작했습니다.")
    if c2.button("■ 중지"):
        res, err = api(base, "POST", "/api/v1/replay/stop")
        st.error(err) if err else st.info("중지했습니다.")

    with st.expander("DB 초기화"):
        ok = st.checkbox("저장된 예측과 알림을 모두 삭제합니다")
        if st.button("초기화 실행", disabled=not ok):
            res, err = api(base, "POST", "/api/v1/replay/reset", params={"confirm": "true"})
            st.error(err) if err else st.success("초기화했습니다.")

    cfg, _ = api(base, "GET", "/api/v1/alerts/config")
    warn = float(cfg["warn"]) if cfg else 60.0
    crit = float(cfg["crit"]) if cfg else 30.0
    hys = float(cfg["hysteresis"]) if cfg else 5.0
    st.subheader("알림 기준 (서버 설정)")
    st.caption(f"🟠 주의: Health Index {warn:g} 미만\n\n🔴 긴급: {crit:g} 미만\n\n회복 판정: 기준 +{hys:g} 이상 (알림 반복 방지)")

    stale_after = st.slider("데이터 끊김 판정 (초)", 10, 3600, 120, step=10,
                            help="마지막 수신 후 이 시간이 지나면 '끊김'(회색)으로 표시합니다. "
                                 "리플레이가 끝나면 새 데이터가 오지 않으므로 시간이 지나면 모든 엔진이 끊김이 됩니다.")
    hpc = st.number_input("1 사이클 = N 시간 (가정값)", min_value=0.1, max_value=1000.0, value=1.0, step=0.5,
                          help="C-MAPSS에는 실제 시간이 없어 화면 표시용 가정값입니다.")

    st.subheader("새로고침")
    auto = st.toggle("자동 새로고침", value=True)
    every = st.slider("주기(초)", 2, 30, 5)


# ---------------------------------------------------------------- 본문
st.title("🏭 Smart Factory PdM 관제")


def render():
    summary, err = api(base, "GET", "/api/v1/rul/summary")
    if err:
        st.error(err)
        return
    rep, svc = summary["replay"], summary["rul_service"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("저장된 예측", f"{summary['rows']:,}")
    m2.metric("관측 엔진", f"{summary['engines']}")
    m3.metric("리플레이", rep.get("status", "-"))
    m4.metric("RUL 추론 서비스", "실행 중" if svc.get("running") else "중지")
    if rep.get("total"):
        st.progress(min(rep["sent"] / rep["total"], 1.0), text=f"리플레이 {rep['sent']:,} / {rep['total']:,}")
    if svc.get("error"):
        st.warning(f"RUL 서비스 오류: {svc['error']}")
    if rep.get("error"):
        st.warning(f"리플레이 오류: {rep['error']}")

    asum, aerr = api(base, "GET", "/api/v1/alerts/summary")
    if asum:
        nc, nw = asum["critical"]["unacked"], asum["warning"]["unacked"]
        if nc:
            st.error(f"🚨 미확인 긴급 알림 {nc}건 (주의 {nw}건) — '알림' 탭에서 확인하세요.")
            #===============================
            res_crit, _ = api(base, "GET", "/api/v1/alerts", params={
                "limit": 10, 
                "unacked_only": "true", 
                "level": "critical"
            })
            
            if res_crit and res_crit.get("data"):
                crit_alerts = res_crit["data"]  # 이미 critical만 들어있음
                
                # 팝업을 아직 안 띄웠다면 모달 창 실행
                if crit_alerts and not st.session_state.get("popup_shown", False):
                    st.session_state.popup_shown = True
                    show_critical_dialog(crit_alerts)
            #======================================

        elif nw:
            st.warning(f"⚠️ 미확인 주의 알림 {nw}건 — '알림' 탭에서 확인하세요.")

    tab_rul, tab_alert, tab_vib, tab_sys = st.tabs(["설비 RUL · Health Index", "알림", "진동 · FFT (준비 중)", "시스템"])

    with tab_rul:
        latest, err = api(base, "GET", "/api/v1/rul/latest")
        warm, _ = api(base, "GET", "/api/v1/rul/warmup")          # 구버전 서버면 None
        if err:
            st.error(err)
        else:
            df = latest_to_df(latest["data"], warn, crit, stale_after)
            nwarm = warm["count"] if warm else 0
            if df.empty and not nwarm:
                st.info("아직 저장된 예측이 없습니다. 사이드바에서 리플레이를 시작하세요.")
            else:
                n = df["status"].value_counts() if not df.empty else {}
                active = df[df["status"] != "stale"] if not df.empty else df
                a, b, c, d, e, f = st.columns(6)
                a.metric(f"🔴 {STATUS_LABEL['danger']}", int(n.get("danger", 0)))
                b.metric(f"🟠 {STATUS_LABEL['warning']}", int(n.get("warning", 0)))
                c.metric(f"🟢 {STATUS_LABEL['ok']}", int(n.get("ok", 0)))
                d.metric(f"⚫ {STATUS_LABEL['stale']}", int(n.get("stale", 0)), help="설정한 시간 동안 데이터가 오지 않은 엔진")
                e.metric("⏳ 수집 중", nwarm, help="윈도우(30사이클)를 채우는 중이라 아직 예측이 없는 엔진")
                f.metric("평균 Health Index", f"{active['health_index'].mean():.1f}" if len(active) else "-")

                if nwarm:
                    with st.expander(f"⏳ 수집 중인 엔진 {nwarm}대 (예측 시작 전)"):
                        win = warm["window"]
                        for w in warm["engines"][:12]:
                            st.progress(min(w["filled"] / win, 1.0), text=f"{w['asset_id']}  {w['filled']}/{win}")
                        if nwarm > 12:
                            st.caption(f"…외 {nwarm - 12}대")

                if not df.empty:
                    f1, f2, f3 = st.columns([2, 2, 3])
                    all_labels = list(STATUS_LABEL.values())
                    sel_labels = f1.multiselect("상태 필터", all_labels, default=all_labels, key="f_status")
                    query = f2.text_input("엔진 검색", key="f_query", placeholder="예: 034")
                    view = filter_df(df, [LABEL_TO_STATUS[x] for x in sel_labels], query)

                    if view.empty:
                        st.info("조건에 맞는 엔진이 없습니다.")
                    else:
                        topn = int(f3.number_input("그래프에 표시할 엔진 수 (위험한 순)", min_value=10, max_value=1000,
                                                   value=100, step=10, key="f_topn"))
                        st.plotly_chart(fleet_bar(view.head(topn), warn, crit, total=len(view)))

                        danger = view[view["status"] == "danger"]
                        if not danger.empty:
                            st.subheader(f"🔴 위험 엔진 ({len(danger)}대)")
                            show = danger[["asset_id", "cycle", "rul_pred", "health_index"]].head(50).rename(
                                columns={"asset_id": "엔진", "cycle": "사이클", "rul_pred": "예측 RUL(사이클)", "health_index": "Health Index"})
                            show_table(show)

                        st.divider()
                        ids = sorted(view["asset_id"])
                        sel = st.selectbox("엔진 선택", ids, index=ids.index(view.iloc[0]["asset_id"]), key="engine")
                        row = view[view["asset_id"] == sel].iloc[0]
                        if row["status"] == "stale":
                            st.warning(f"⚫ 데이터 끊김: 마지막 수신 {format_age(row.get('age_sec'))} 전 — 아래 값은 마지막으로 받은 시점의 값입니다.")
                        left, right = st.columns([1, 2])
                        with left:
                            st.plotly_chart(gauge(row["health_index"], warn, crit))
                            st.metric("예측 RUL", f"{row['rul_pred']:.0f} 사이클")
                            st.caption(f"≈ {cycles_to_hours(row['rul_pred'], hpc):.0f} 시간 (가정: 1사이클 = {hpc}시간)")
                            if pd.notna(row.get("rul_true")):
                                st.metric("실제 RUL (데모용 정답)", f"{row['rul_true']:.0f} 사이클")
                        with right:
                            hist, err = api(base, "GET", f"/api/v1/rul/history/{sel}", params={"limit": 200})
                            if err:
                                st.error(err)
                            else:
                                hdf = history_to_df(hist["data"])
                                if hdf.empty:
                                    st.info("이력이 없습니다.")
                                else:
                                    st.plotly_chart(history_fig(hdf, sel, warn, crit))

    with tab_alert:
        if not asum:
            st.info(f"알림 정보를 불러오지 못했습니다. {aerr or ''}")
        else:
            k1, k2, k3, k4 = st.columns([1, 1, 1, 1])
            k1.metric("🔴 긴급 (미확인/전체)", f"{asum['critical']['unacked']} / {asum['critical']['total']}")
            k2.metric("🟠 주의 (미확인/전체)", f"{asum['warning']['unacked']} / {asum['warning']['total']}")
            only = k3.checkbox("미확인만 보기", value=True, key="alerts_unacked")
            if k4.button("모두 확인 처리"):
                api(base, "POST", "/api/v1/alerts/ack-all")
                st.rerun()
            res, err = api(base, "GET", "/api/v1/alerts", params={"limit": 30, "unacked_only": str(only).lower()})
            if err:
                st.error(err)
            elif not res["data"]:
                st.success("표시할 알림이 없습니다.")
            else:
                for a in res["data"]:
                    c = st.columns([1.1, 1.6, 6, 1.2])
                    c[0].write("🔴 긴급" if a["level"] == "critical" else "🟠 주의")
                    c[1].write(f"{a['asset_id']} · {a['cycle']}사이클")
                    c[2].write(a["message"])
                    if a["acknowledged"]:
                        c[3].caption("확인됨")
                    elif c[3].button("확인", key=f"ack_{a['id']}"):
                        api(base, "POST", f"/api/v1/alerts/{a['id']}/ack")
                        st.rerun()

    with tab_vib:
        st.info("아직 구현 전입니다. 진동 처리 워커(FFT·특징 추출·이상 점수)가 완성되면 이 탭에 다음이 들어갑니다.")
        st.markdown(
            "- 설비(베어링)별 **실시간 FFT 스펙트로그램** (시간 × 주파수)\n"
            "- 결함 주파수(BPFO/BPFI) 표시선과 엔벨로프 스펙트럼\n"
            "- 정상 이탈 점수(Autoencoder)와 진동 기반 Health Index")

    with tab_sys:
        st.json(summary)


if auto and hasattr(st, "fragment"):
    st.fragment(run_every=every)(render)()      # 이 영역만 주기적으로 갱신 (Streamlit 1.37+)
else:
    st.button("새로고침")
    render()
