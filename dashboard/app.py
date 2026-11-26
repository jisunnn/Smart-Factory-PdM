"""Smart Factory PdM 관제 대시보드 (Streamlit + Plotly).

실행:  streamlit run dashboard/app.py      (FastAPI 서버가 먼저 떠 있어야 함)
화면은 FastAPI 의 /api/v1/* 만 호출한다 (DB에 직접 접속하지 않음).
"""
import os

import json
import numpy as np
import pandas as pd
import requests
import streamlit as st

from charts import fleet_bar, gauge, history_fig, proba_fig, spectrogram_fig, spectrum_fig, vib_hi_fig
from logic import (CLASS_KO, LABEL_TO_STATUS, STATUS_LABEL, alert_where, cwru_files_to_df, cycles_to_hours, filter_df, format_age,
                   history_to_df, latest_to_df, spectra_to_arrays, vib_history_to_df, vib_latest_to_df)


st.set_page_config(page_title="Smart Factory PdM", page_icon="🏭", layout="wide")
API_DEFAULT = os.getenv("PDM_API_URL", "http://localhost:8000")

#========================================알림 추가=====

def _ack_alert(base: str, alert_id: int):
    _, err = api(base, "POST", f"/api/v1/alerts/{alert_id}/ack")
    if err:
        st.warning(f"확인 처리 실패 (ID {alert_id}): {err}")


@st.dialog("🚨 긴급 설비 점검 알림 (Critical)", width="large")
def critical_alert_dialog(base: str, critical_items: list[dict]):
    alert_count = len(critical_items)
    st.error(f"긴급 설비 이상이 감지되었습니다! 즉각적인 조치가 필요합니다.")
    st.caption("각 설비 카드의 '확인 완료'를 누르면 해당 알림만 개별 처리됩니다.")

    cols = st.columns(2)
    for idx, item in enumerate(critical_items):
        alert_id = item["id"]
        engine = item.get("asset_id")
        cycle = item.get("cycle", "-")
        hi = item.get("health_index")
        rul = item.get("rul_pred", "-")

        with cols[idx % 2]:
            with st.container(border=True):
                st.markdown(f"🔴 **{engine}**")
                if cycle and rul : 
                    st.caption(f"가동 사이클: {cycle}")
                    st.markdown(
                        f"Health Index : :red[**{hi}**] :gray[(기준 30 미만)]  \n"
                        f"예상 잔여 사이클 : 약 :red[**{rul}**] "
                    )
                else:                   
                    st.markdown(f"Health Index : :red[**{hi:.1f}**] :gray[(기준 30 미만)]  \n")

                if st.button(
                    "확인 완료",
                    key=f"ack_alert_{alert_id}",
                    width='stretch',
                ):
                    _ack_alert(base, alert_id)
                    
                    # 아직 확인하지 않은 나머지 알림들은 다시 팝업에 표시되도록 기록에서 제외
                    remaining_ids = {
                        x["id"] for x in critical_items if x["id"] != alert_id
                    }
                    st.session_state["shown_critical_ids"] -= remaining_ids
                    
                    st.rerun()
#========================================알림 추가=====

# ---------------------------------------------------------------- API 호출
@st.cache_resource
def get_http_session() -> requests.Session:
    """앱 전체에서 HTTP 연결(Keep-Alive)을 재사용하기 위한 단일 세션"""
    return requests.Session()


def api(base, method, path, **kw):
    session = get_http_session()
    try:
        r = session.request(method, f"{base}{path}", timeout=10, **kw)
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




# ===================================================== 캐시가 적용된 데이터 조회 함수들 추가==============
@st.cache_data(ttl=5, show_spinner=False)  # 5초 동안은 동일한 경로의 GET 요청 결과를 캐시, ui에 안보이게 하기 옵션 : show_spinner=False 
def get_cached_api_get(base_url, path):
    res, err = api(base_url, "GET", path)
    return res, err

@st.cache_data(ttl=10, show_spinner=False)
def get_cached_history(base_url, path, limit_val):
    res, err = api(base_url, "GET", path, params={"limit": limit_val})
    return res, err


@st.cache_data(ttl=30, show_spinner=False)  # 진동 데이터는 조금 무거우니 ttl을 30초~1분 정도로
def get_cached_spectrum(base_url, bearing_id, limit_count):
    res, err = api(base_url, "GET", f"/api/v1/vib/spectrum/{bearing_id}", params={"limit": limit_count})
    return res, err


@st.cache_data(ttl=5, show_spinner=False)
def get_cached_alerts(base_url: str, unacked_only: bool, limit_val: int = 30):
    res, err = api(
        base_url,
        "GET",
        "/api/v1/alerts",
        params={"limit": limit_val, "unacked_only": str(unacked_only).lower()},
    )
    return res, err
#-------------------------===================================
@st.cache_data(ttl=5, show_spinner=False)
def get_cached_alert_summary(base_url: str):
    return api(base_url, "GET", "/api/v1/alerts/summary")

#---------config api 캐시 길게 처리---------------------------------------------------
@st.cache_data(ttl=600, show_spinner=False)
def get_cached_config(base_url, path):
    res, err = api(base_url, "GET", path)
    if err:
        get_cached_config.clear()  # 실패 시 캐싱하지 않고 다음번에 재시도
    return res, err




def show_table(df):
    """표 출력. pyarrow 가 깨진 환경에서는 HTML 표로 대체한다 (st.dataframe 은 pyarrow 필요)."""
    try:
        st.dataframe(df, hide_index=True)
    except ImportError:
        st.markdown(df.to_html(index=False, border=0, float_format=lambda v: f"{v:.1f}"), unsafe_allow_html=True)
# --------------------------------------------------------------------------


#-------엔진 health_index 그래프 함수----------------------------------------------------------
def health_index_show(df):
    if not df.empty:                  
        all_labels = list(STATUS_LABEL.values())
        
        # 1. 상태 필터만 나오게
        sel_labels = st.multiselect("상태 필터", all_labels, default=all_labels, key="f_status")

        # 2. 화면엔 안 보이지만 기존 로직 유지를 위한 숨김 값 (검색어 없음, 상위 100대 고정)
        query = "" 
        topn = 100

        # 엔진 검색 기능 및 엔진 수 조절이 필요할 경우
        #f1, f2, f3 = st.columns([2, 2, 3])
        #all_labels = list(STATUS_LABEL.values())
        #sel_labels = f1.multiselect("상태 필터", all_labels, default=all_labels, key="f_status")
        #query = f2.text_input("엔진 검색", key="f_query", placeholder="예: 034")
        view = filter_df(df, [LABEL_TO_STATUS[x] for x in sel_labels], query)

        if view.empty:
            st.info("조건에 맞는 엔진이 없습니다.")
            return view
        else:
            #topn = int(f3.number_input("그래프에 표시할 엔진 수 (위험한 순)", min_value=10, max_value=100,
            #                           value=100, step=10, key="f_topn"))
            st.plotly_chart(fleet_bar(view.head(topn), warn, crit, total=len(view)))

            danger = view[view["status"] == "danger"]
            if not danger.empty:
                st.subheader(f"🔴 위험 엔진 ({len(danger)}대)")
                show = danger[["asset_id", "cycle", "rul_pred", "health_index"]].head(50).rename(
                    columns={"asset_id": "엔진", "cycle": "사이클", "rul_pred": "예측 RUL(사이클)", "health_index": "Health Index"})
                show_table(show)
            engine_status_show(view)

                       
#--------각 엔진 상세 조회 함수------
@st.fragment
def engine_status_show(view):
    
    st.divider()
    ids = sorted(view["asset_id"])
    sel = st.selectbox("엔진 선택", ids, index=ids.index(view.iloc[0]["asset_id"]), key="engine")
                            
    row = view[view["asset_id"] == sel].iloc[0]
    if row["status"] == "stale":
        st.warning(f"⚫ 데이터 끊김: 마지막 수신 {format_age(row.get('age_sec'))} 전 — 아래 값은 마지막으로 받은 시점의 값입니다.")
    left, right = st.columns([1, 2])
    with left:
        st.plotly_chart(gauge(row["health_index"], warn, crit), width='stretch', config={"displayModeBar": False})
        st.metric("예측 RUL", f"{row['rul_pred']:.0f} 사이클")
        st.caption(f"≈ {cycles_to_hours(row['rul_pred'], hpc):.0f} 시간 (가정: 1사이클 = {hpc}시간)")
        if pd.notna(row.get("rul_true")):
            st.metric("실제 RUL (데모용 정답)", f"{row['rul_true']:.0f} 사이클")
    with right:
        hist, err = get_cached_history(base, f"/api/v1/rul/history/{sel}", 100)
        if err:
            st.error(err)
        else:
            hdf = history_to_df(hist["data"])
            if hdf.empty:
                st.info("이력이 없습니다.")
            else:
                st.plotly_chart(history_fig(hdf, sel, warn, crit), width='stretch', config={"displayModeBar": False}) #, "scrollZoom": False 

#-------ims 스펙트럼 그래프 함수-------------------------------------------------------
@st.fragment
def ims_spectrum_show(vcfg, vdf):
    bsel = st.selectbox("베어링 선택", [int(b) for b in vdf["bearing"]], key="vib_bearing", format_func=lambda b: f"베어링 {b}")
    nfiles = st.slider("스펙트로그램에 쓸 최근 파일 수", 30, 600, 150, step=30, key="vib_nfiles",
                                help="파일 1개 = 1초 파형 (10분 간격 기록). 150개 ≈ 25시간")
    sp, serr = get_cached_spectrum(base, bsel, nfiles)  # 캐시 적용된 api 호출
    fault = (vcfg or {}).get("fault_hz") or {}
    step = (sp or {}).get("step_hz", 2)
    arr = spectra_to_arrays(sp["data"], step) if sp else None
    if serr:
        st.error(serr)
    elif arr is None:
        st.info("이 베어링의 스펙트럼이 아직 없습니다.")
    else:
        xs, freqs, Z = arr
        left, right = st.columns(2)
        with left:
            st.markdown("**엔벨로프 스펙트럼 (현재 vs 이전)**")
            ref = Z[:, 0] if Z.shape[1] > 1 else None
            st.plotly_chart(spectrum_fig(freqs, Z[:, -1], f"현재 ({xs[-1]:.0f}h)", ref, f"이전 ({xs[0]:.0f}h)", fault))
        with right:
            st.markdown("**스펙트로그램 (시간 x 주파수)**")
            st.plotly_chart(spectrogram_fig(xs, freqs, Z, fault))
        st.caption("점선 = 결함 주파수(BPFO 외륜, BPFI 내륜, BSF 볼, FTF 케이지). 결함이 진행되면 해당 주파수 부근의 진폭이 커집니다.")
        cur = vdf[vdf["bearing"] == bsel].iloc[0]
        z1, z2, z3 = st.columns(3)
        z1.metric("외륜(BPFO) 피크", f"{cur['z_env_bpfo']:+.1f} σ", help="정상 구간 대비 표준편차 배수")
        z2.metric("내륜(BPFI) 피크", f"{cur['z_env_bpfi']:+.1f} σ")
        z3.metric("볼(BSF) 피크", f"{cur['z_env_bsf']:+.1f} σ")
        st.caption("의심 결함은 규칙 기반 추정입니다: Health Index가 주의 기준 아래일 때, 세 결함 주파수 중 정상 대비 가장 크게 늘어난 것을 표시합니다. "
                    "임계값(3σ 이상, 1위와 2위 차이 1.5σ 이상)은 검증 전 시작값이며, 이 데이터(IMS Set 2)에서 검증 가능한 결함은 외륜뿐입니다.")

# -------cwru 그래프 함수------------------------------
def cwru_show(clat, ccfg, cfiles):
    row = (clat or {}).get("data")
    if row:
        c1_, c2_, c3_ = st.columns(3)
        c1_.metric("재생 중인 파일", f"{row['file_id']}", help="CWRU 파일 번호 (파일마다 정상/결함 유형이 다름)")
        c2_.metric("정답 (평가용)", CLASS_KO.get(row["label_true"], row["label_true"]), help="분류기 입력이 아니라 정확도 비교용입니다.")
        c3_.metric("분류 결과 (최근 10개 윈도우 투표)", CLASS_KO.get(row["vote_label"], row["vote_label"]))
        if ccfg and ccfg.get("classes") and row.get("proba"):
            st.plotly_chart(proba_fig(ccfg["classes"], row["proba"], CLASS_KO))
    fd = cfiles["data"]
    tot = sum(d["n"] for d in fd)
    st.metric("전체 단일 윈도우 정확도", f"{100 * sum(d['correct'] for d in fd) / max(tot, 1):.1f}%",
                help=f"윈도우 {tot:,}개 기준")
    show_table(cwru_files_to_df(fd))
    st.caption("CWRU는 쉬운 벤치마크라 높은 정확도가 모델의 우수성을 뜻하지는 않습니다. 이 화면은 서비스 경로에서 Colab 평가와 같은 결과가 재현되는지를 보여 줍니다.")



# ------작업 지시서 부분 대시보드 함수
def _render_workorder_card(wo: dict):
    """work_orders DB 레코드(wo)와 payload를 구조화된 UI 카드로 렌더링"""
    # payload가 문자열(JSON)로 내려올 경우를 대비한 안전 파싱
    p = wo.get("payload") or {}
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except Exception:
            p = {}

    level = wo.get("level", "warning")
    is_crit = level == "critical"

    st.divider()

    # 1. 최상단 긴급/주의 배너 (대응 시한 강조)
    sla = p.get("sla") or ("즉시(1시간 이내) 가동 중지 검토 및 부품 교체 착수" if is_crit else "24시간 이내 현장 점검")
    if is_crit:
        st.error(f"🚨 **[긴급 / Critical]** 필수 대응 시한: **{sla}**")
    else:
        st.warning(f"⚠️ **[주의 / Warning]** 필수 대응 시한: **{sla}**")

    # 2. 핵심 메타 지표 4단 카드 (DB 컬럼 + payload 조합)
    hi_val = p.get("hi")
    hi_str = f"{float(hi_val):.1f}" if hi_val is not None else "-"
    rul_val = p.get("rul")
    rul_str = f"{float(rul_val):.1f}h" if rul_val is not None else "해당 없음"

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("설비 ID", wo.get("asset_id") or "-")
    c2.metric("Health Index", hi_str)
    c3.metric("예측 RUL", rul_str)
    c4.metric("SOP 코드", wo.get("sop_code") or "-")

    # 부가 메타 정보 한 줄 요약 (DB의 source, cycle, status, llm_used 활용)
    issued_at = p.get("issued_at") or wo.get("alert_time") or wo.get("created_at") or "-"
    source_map = {"vib": "진동 HI·결함분류", "rul": "RUL 예측"}
    source_str = source_map.get(wo.get("source"), wo.get("source") or "-")
    cycle_str = f"Cycle #{wo['cycle']}" if wo.get("cycle") is not None else ""
    gen_badge = "🤖 AI 생성" if wo.get("llm_used") else f"📄 템플릿 ({wo.get('fallback_reason') or '기본'})"

    meta_items = [f"발행 일시: {issued_at}", f"분석 경로: {source_str}", f"생성 방식: {gen_badge}"]
    if cycle_str:
        meta_items.insert(2, cycle_str)
    st.caption("  |  ".join(meta_items))

    # 3. AI 상황 요약 및 우선 조치 이유 (상단 배치)
    summary = p.get("summary")
    rationale = p.get("rationale")
    if summary or rationale:
        with st.container(border=True):
            st.markdown("#### 🤖 AI 상황 요약 및 판단 근거")
            if summary:
                st.markdown(f"**[상황 요약]** {summary}")
            if rationale:
                st.info(f"**[우선 조치 이유]** {rationale}")

    # 4. 좌측: 조치 권고사항 / 우측: 안전 작업 절차(LOTO) 2단 분할
    steps = p.get("steps") or []
    loto_steps = p.get("loto") or []

    if steps or loto_steps:
        col_left, col_right = st.columns([1.2, 1])

        with col_left:
            st.markdown("#### 🔧 조치 권고사항 (매뉴얼 원문)")
            for idx, s in enumerate(steps, 1):
                if isinstance(s, dict):
                    is_pri = s.get("priority") or s.get("selected")
                    stage = s.get("stage", "")
                    text = s.get("text", "")
                    ref = s.get("ref", "")
                    badge = " ⭐ `우선`" if is_pri else ""
                    with st.container(border=True):
                        st.markdown(f"**{idx}. [{stage}]**{badge}")
                        st.write(text)
                        if ref:
                            st.caption(f"출처: {ref}")
                else:
                    with st.container(border=True):
                        st.write(f"**{idx}.** {s}")

        with col_right:
            st.markdown("#### ⚠️ 안전 작업 절차 (LOTO)")
            with st.container(border=True):
                if loto_steps:
                    for i, l_step in enumerate(loto_steps, 1):
                        # 딕셔너리인 경우 text만 깔끔하게 추출
                        if isinstance(l_step, dict):
                            l_text = l_step.get("text", str(l_step))
                        else:
                            l_text = str(l_step)
                        st.checkbox(f"{i}. {l_text}", key=f"loto_{wo.get('id', issued_at)}_{i}")
                else:
                    st.write("매뉴얼 4장 표준 LOTO 절차 준수")
                if p.get("verification"):
                    st.divider()
                    st.markdown(f"**필수 안전 및 검증 기준:** {p['verification']}")
    else:
        # payload에 steps가 없는 구버전 데이터일 경우 기존 text로 안전하게 폴백
        st.markdown(wo.get("text", ""))

    # 5. 참조 매뉴얼 근거 문단 (접기 기능으로 본문 깔끔하게 유지)
    evidence = p.get("evidence") or []
    if evidence:
        with st.expander("📚 참조 매뉴얼 근거 문단 보기"):
            for ev in evidence:
                st.write(f"- [ **SOP ID** : {ev['id']}], [ **항목** : {ev['ref']}], [ **내용** : {ev['text']}]")


#-----알림 워커 함수
@st.fragment()
def critical_alert_watcher():
    asum, aerr = api(base, "GET", "/api/v1/alerts/summary")
    if asum:
        nc, nw = asum["critical"]["unacked"], asum["warning"]["unacked"]
        if nc:
            st.error(f"🚨 미확인 긴급 알림 {nc}건 (주의 {nw}건) — '알림' 탭에서 확인하세요.")
            #====================알림===========
            res_crit, _ = api(base, "GET", "/api/v1/alerts", params={
                "limit": max(nc, 1), # 확인 안된 모든 긴급알림
                "unacked_only": "true",
                "level": "critical"
                })
            items = (res_crit or {}).get("data", [])

            if items:
                # 2. 아직 팝업으로 띄운 적 없는 새 알림만 골라내기
                new_items = [
                    item for item in items 
                    if item["id"] not in st.session_state["shown_critical_ids"]
                ]

                # 3. 새로운 긴급 알림이 감지되었을 때만 팝업 실행
                if new_items:
                    for item in new_items:
                        st.session_state["shown_critical_ids"].add(item["id"])
                    
                    critical_alert_dialog(base, new_items)         
            #======================================         
        elif nw:
            st.warning(f"⚠️ 미확인 주의 알림 {nw}건 — '알림' 탭에서 확인하세요.")



#-----설비 RUL · Health Index 탭 실행 함수--------------------------------------------------------------------
@st.fragment
def rul_tab_sow():
    # st.button("🔄", key="btn_refresh_rul")
    latest, err = get_cached_api_get(base, "/api/v1/rul/latest")
    warm, _ = get_cached_api_get(base, "/api/v1/rul/warmup")          # 구버전 서버면 None
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

            # 엔진 health_index 그래프 조회 함수 및  엔진 상세 조회 그래프 함수               
            health_index_show(df)


# ---------알림 탭 실행 함수--------------------------------------------------------------
@st.fragment
def alerts_tab_sow():  
    asum, aerr = get_cached_alert_summary(base)
    
    if not asum:
        st.info(f"알림 정보를 불러오지 못했습니다. {aerr or ''}")
        return

    k1, k2, k3, k4 = st.columns([1.2, 1.2, 1, 1])
    k1.metric("🔴 긴급 (미확인/전체)", f"{asum['critical']['unacked']} / {asum['critical']['total']}")
    k2.metric("🟠 주의 (미확인/전체)", f"{asum['warning']['unacked']} / {asum['warning']['total']}") 
    only = k3.checkbox("미확인만 보기", value=True, key="alerts_unacked")
    
    if k4.button("전체 모두 확인", width="stretch"):
        _, err = api(base, "POST", "/api/v1/alerts/ack-all")
        if err:
            st.warning(f"일괄 확인 실패: {err}")
        else:
            get_cached_alerts.clear()
            get_cached_alert_summary.clear()
            st.rerun(scope="fragment")

    res, err = get_cached_alerts(base, only, 100)
    
    if err:
        st.error(err)
        return
    if not res or not res.get("data"):
        st.success("표시할 알림이 없습니다.")
        return

    # 1. 데이터를 표(DataFrame) 형태로 변환
    rows = []
    for a in res["data"]:
        rows.append({
            "id": a["id"],
            "선택": False,  # 체크박스 컬럼
            "상태": "✅ 확인됨" if a["acknowledged"] else "⏳ 미확인",
            "알림 단계": "🔴 긴급" if a["level"] == "critical" else "🟠 주의",
            "대상": alert_where(a),
            "상세 내용": a["message"],
            "acknowledged": a["acknowledged"],
        })
    df_alerts = pd.DataFrame(rows)

    # 2. 체크박스 편집 가능한 단일 표 렌더링 (수십 개 루프보다 수십 배 빠름)
    edited_df = st.data_editor(
        df_alerts[["선택", "상태", "알림 단계", "대상", "상세 내용"]],
        hide_index=True,
        width="stretch",
        height=420,
        disabled=["상태", "알림 단계", "대상", "상세 내용"],  # 체크박스 외 컬럼은 수정 불가
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", default=False, width="small"),
            "상태": st.column_config.TextColumn("상태", width="small"),
            "알림 단계": st.column_config.TextColumn("알림 단계", width="small"),
            "대상": st.column_config.TextColumn("대상", width="medium"),
            "상세 내용": st.column_config.TextColumn("상세 내용", width="large"),
        },
        key="alerts_editor_table",
    )

    # 3. 체크된 항목만 골라서 확인 처리 실행
    selected_indices = edited_df[edited_df["선택"]].index.tolist()
    if selected_indices:
        # 이미 확인된 항목 제외하고 미확인 ID만 추출
        target_ids = [
            int(df_alerts.loc[idx, "id"])
            for idx in selected_indices
            if not df_alerts.loc[idx, "acknowledged"]
        ]
        
        if st.button(f"선택한 알림 확인 처리 ({len(target_ids)}건)", type="primary"):
            for aid in target_ids:
                api(base, "POST", f"/api/v1/alerts/{aid}/ack")
            get_cached_alerts.clear()
            get_cached_alert_summary.clear()
            st.rerun(scope="fragment")

# -------진동 · FFT 탭 함수-----------------------------------------------------------
@st.fragment
def vib_tab_sow():
    st.button("🔄", key="btn_refresh_vib")
    vcfg, _ = get_cached_config(base, "/api/v1/vib/config")
    vlat, verr = get_cached_api_get(base, "/api/v1/vib/latest")
    vdf = vib_latest_to_df(vlat["data"], warn, crit, stale_after) if vlat else pd.DataFrame()
    if verr:
        st.error(verr)
    elif vdf.empty:
        st.info("아직 진동 데이터가 없습니다. 사이드바에서 데이터를 'IMS Set 2'로 선택하고 ▶ 시작을 누르세요.")
    else:
        icon = {"danger": "🔴", "warning": "🟠", "ok": "🟢", "stale": "⚫"}
        cols = st.columns(len(vdf))
        for col, (_, r) in zip(cols, vdf.iterrows()):
            col.metric(f"{icon[r['status']]} 베어링 {int(r['bearing'])}", f"{r['health_index']:.1f}",
                        help=f"이상 점수 비율 {r['ratio']:.2f} (1 이하면 정상 범위) · 경과 {r['elapsed_h']:.1f}시간 · 마지막 수신 {format_age(r.get('age_sec'))} 전")
        sus_cols = st.columns(len(vdf))
        for col, (_, r) in zip(sus_cols, vdf.iterrows()):
            s = r.get("suspect")
            if r["status"] == "stale":
                col.caption("데이터 끊김")
            elif isinstance(s, dict):
                col.caption(f"🔍 의심 결함: **{s['label']}**" + (f" ({s['z']:+.1f}σ)" if s.get("z") is not None else ""))
            else:
                col.caption("정상 범위")
        series = {}
        for b in vdf["bearing"]:
            h, _ = get_cached_history(base, f"/api/v1/vib/history/{int(b)}", 700)
            if h:
                hd = vib_history_to_df(h["data"])
                if not hd.empty:
                    series[int(b)] = hd
        st.subheader("베어링별 Health Index 추이")
        if series:
            st.plotly_chart(vib_hi_fig(series, warn, crit))

        st.divider()       
        # 스펙트럼 그래프 함수 
        ims_spectrum_show(vcfg, vdf)

# ----결함 분류 (CWRU)-------------------------------------------------------------------------
@st.fragment
def cwru_tab_sow():
    st.button("🔄", key="btn_refresh_cwru")
    st.caption("CWRU Random Forest 분류기: 0HP 데이터로 학습한 모델이 학습에 쓰지 않은 3HP 파일의 윈도우를 정상·내륜·볼·외륜으로 분류합니다. IMS 신호에는 쓰지 않습니다.")
    cfiles, cerr = get_cached_api_get(base, "/api/v1/cwru/files")
    clat, _ = get_cached_api_get(base, "/api/v1/cwru/latest")
    ccfg, _ = get_cached_config(base, "/api/v1/cwru/config")
    if cerr:
        st.error(cerr)
    elif not cfiles or not cfiles["data"]:
        st.info("아직 CWRU 분류 결과가 없습니다. 사이드바에서 데이터를 'CWRU 3HP'로 선택하고 ▶ 시작을 누르세요.")
    else:
        cwru_show(clat, ccfg, cfiles)
        

# --에이전트 탭-----------------------------------
@st.fragment
def agent_tab_sow():
    asm, aerr2 = get_cached_api_get(base, "/api/v1/agent/summary")
    if aerr2 or not asm:
        st.info(f"작업 지시서 에이전트 정보를 불러오지 못했습니다. {aerr2 or ''}")
    else:
        ag = asm.get("agent_service") or {}
        with st.expander(f"📊 에이전트 운영 현황 요약 (총 {asm.get('total', 0)}건 · AI {asm.get('llm', 0)}건 · 템플릿 {asm.get('template', 0)}건)", expanded=False):
            w1, w2, w3, w4 = st.columns(4)
            w1.metric("작업 지시서", asm["total"])
            w2.metric("AI 생성", asm["llm"])
            w3.metric("템플릿", asm["template"], help="LLM을 쓰지 않았거나 검증에 실패해 매뉴얼 원문 템플릿으로 만든 지시서")
            w4.metric("검색 방식", {"gemini": "Gemini 임베딩", "keyword": "키워드"}.get(ag.get("retrieval_mode"), "-"))
            if asm.get("fallback_reasons"):
                st.markdown("**템플릿 대체 사유:**")
                for k, v in asm["fallback_reasons"].items():
                    st.write(f"- {k}: {v}건")

        wl, werr = get_cached_history(base, "/api/v1/agent/workorders", 50)
        if werr:
            st.error(werr)
        elif not wl["data"]:
            st.info("아직 작업 지시서가 없습니다. 알림이 발생하면 자동으로 생성됩니다. (서버 시작 후 새로 생긴 알림만 처리합니다)")
        else:
            def _label(r):
                p = r["payload"]
                return (f"{p['issued_at']} · {r['asset_id']} · {'긴급' if r['level'] == 'critical' else '주의'} · "
                        f"{r['sop_code']} · {'AI' if r['llm_used'] else '템플릿'}")

            pick = st.selectbox("작업 지시서 선택 (최근 50건)", range(len(wl["data"])), format_func=lambda i: _label(wl["data"][i]), key="wo_pick")
            
           # 작업지시서 대시보드 함수 
            _render_workorder_card(wl["data"][pick])
            
            st.caption("ℹ️ 시한·SOP 코드·조치 문장은 매뉴얼 원문이며, AI는 상황 요약과 우선 조치 표시만 담당합니다. 검증에 실패하면 템플릿으로 대체됩니다.")

@st.fragment
def main_content_area():
    menu = st.segmented_control(
        "메뉴 선택",
        options=["설비 RUL · Health Index", "알림", "진동 · FFT", "결함 분류 (CWRU)", "AI 작업지시 에이전트"],
        default="설비 RUL · Health Index",
        label_visibility="collapsed",
        key="main_menu_nav"
    )

    if menu == "설비 RUL · Health Index":
        rul_tab_sow()
    elif menu == "알림":
        alerts_tab_sow()
    elif menu == "진동 · FFT":
        vib_tab_sow()
    elif menu == "결함 분류 (CWRU)":
        cwru_tab_sow()
    elif menu == "AI 작업지시 에이전트":
        agent_tab_sow()


#----관리자 탭 만들면 사용----
#@st.fragment
#def system_tab_sow(summary):



# ---------------------------------------------------------------- 사이드바
with st.sidebar:
    st.header("설정")
    base = st.text_input("API 주소", API_DEFAULT).rstrip("/")
    
    st.subheader("데이터 리플레이")
    kind = st.selectbox("데이터", ["cycle", "vibration", "cwru"],
                        format_func=lambda k: {"cycle": "C-MAPSS FD001 (엔진 RUL)", "vibration": "IMS Set 2 (베어링 4개 진동)",
                                               "cwru": "CWRU 3HP (베어링 결함 분류)"}[k],
                        help="IMS: 파일 984개 x 베어링 4개 = 3,936건(보통 속도 약 3분). CWRU: 3HP 파일의 2048샘플 윈도우를 재생합니다.")
    speed = st.select_slider("재생 속도", options=["느림", "보통", "빠름"], value="빠름",
                             help="느림 0.2초 / 보통 0.05초 / 빠름 0.005초 (메시지 간격)")
    interval = {"느림": 0.2, "보통": 0.05, "빠름": 0.005}[speed]
    loop = st.checkbox("끝나면 처음부터 반복")
  
    c1, c2 = st.columns(2)
    if c1.button("▶ 시작"):
        res, err = api(base, "POST", "/api/v1/replay/start", json={"kind": kind, "interval": interval, "loop": loop})
        if err:
            st.error(err)
        else:
            st.session_state["is_replaying"] = True 
            st.success("리플레이를 시작했습니다.")
    if c2.button("■ 중지"):
        res, err = api(base, "POST", "/api/v1/replay/stop")
        if err:
            st.error(err)
        else:
            st.session_state["is_replaying"] = False
            st.info("중지했습니다.")

    with st.expander("DB 초기화"):
        ok = st.checkbox("저장된 예측과 알림을 모두 삭제합니다")
        if st.button("초기화 실행", disabled=not ok):
            res, err = api(base, "POST", "/api/v1/replay/reset", params={"confirm": "true"})
            if err:
                st.error(err)  
            else:
                st.success("초기화했습니다.")
                st.rerun()

    cfg, _ = get_cached_api_get(base, "/api/v1/alerts/config")

    warn = float(cfg["warn"]) if cfg else 60.0
    crit = float(cfg["crit"]) if cfg else 30.0
    hys = float(cfg["hysteresis"]) if cfg else 5.0
    st.subheader("알림 기준 (서버 설정)")
    st.caption(f"🟠 주의: Health Index {warn:g} 미만\n\n🔴 긴급: {crit:g} 미만\n\n회복 판정: 기준 +{hys:g} 이상 (알림 반복 방지)")

    stale_after = st.slider("데이터 끊김 판정 (초)", 120, 5000, 3600, step=10,
                            help="마지막 수신 후 이 시간이 지나면 '끊김'(회색)으로 표시합니다. "
                                 "리플레이가 끝나면 새 데이터가 오지 않으므로 시간이 지나면 모든 엔진이 끊김이 됩니다.")
    hpc = st.number_input("1 사이클 = N 시간 (가정값)", min_value=0.1, max_value=1000.0, value=1.0, step=0.5,
                          help="C-MAPSS에는 실제 시간이 없어 화면 표시용 가정값입니다.")

    #st.subheader("새로고침")
    #auto = st.toggle("자동 새로고침", value=False)
    #every = st.slider("주기(초)", 2, 30, 5)


# ---------------------------------------------------------------- 본문
st.title("🏭 Smart Factory PdM 관제")

# 1. 이미 팝업으로 띄운 ID 기록용 집합 초기화
if "shown_critical_ids" not in st.session_state:
    st.session_state["shown_critical_ids"] = set()



poll_interval = 1 if st.session_state.get("is_replaying", False) else None

@st.fragment(run_every=poll_interval)
def render():
    # st.button("🔄", key="btn_refresh")
    summary, err = get_cached_api_get(base, "/api/v1/rul/summary")
    if err:
        st.error(err)
        return
    rep, svc = summary["replay"], summary["rul_service"]

    vsum, _ = get_cached_api_get(base, "/api/v1/vib/summary")           # 구버전 서버면 None
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("저장된 예측", f"{summary['rows']:,}")
    m2.metric("관측 엔진", f"{summary['engines']}")
    m3.metric("리플레이", rep.get("status", "-"))
    m4.metric("RUL 추론 서비스", "실행 중" if svc.get("running") else "중지")
    vs = (vsum or {}).get("vib_service") or {}
    m5.metric("진동 추론 서비스", "-" if not vsum else ("실행 중" if vs.get("running") else "중지"))
    # if rep.get("total"):
    if rep.get("status") == "running" and rep.get("total"):
        st.progress(min(rep["sent"] / rep["total"], 1.0), text=f"리플레이 {rep['sent']:,} / {rep['total']:,}")
        
    if svc.get("error"):
        st.warning(f"RUL 서비스 오류: {svc['error']}")
    if rep.get("error"):
        st.warning(f"리플레이 오류: {rep['error']}")

    critical_alert_watcher()
    



# 1. 상단 상태/알림 영역 (auto가 켜졌을 때만 주기 갱신)
#if auto:
#    @st.fragment(run_every=every)
#    def _top_live():
#        render()
#    _top_live()

render()
main_content_area()

# 2. 탭 선택 및 선택된 탭 프래그먼트 호출
#tab_rul, tab_alert, tab_vib, tab_cwru, tab_agent = st.tabs(["설비 RUL · Health Index", "알림", "진동 · FFT", "**결함 분류 (CWRU)**", "**AI 작업지시 에이전트**"]) # 탭 이름을 설비이름으로 변경할 지 고려
#with tab_rul:
#    rul_tab_sow()

#with tab_alert:
#    alerts_tab_sow()   

#with tab_vib:
#    vib_tab_sow()

#with tab_cwru:
#    cwru_tab_sow()
#with tab_agent:
#    agent_tab_sow()











