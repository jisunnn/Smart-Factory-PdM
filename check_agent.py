"""작업 지시서 에이전트 점검 (DB·Kafka 불필요).

사용 (프로젝트 루트, venv 활성화):
  python check_agent.py             # 매뉴얼 읽기 → Gemini 연결 확인(키가 있으면) → 샘플 알림 3종으로 작업 지시서 생성
  python check_agent.py --no-llm    # Gemini 없이 템플릿 경로만 확인
"""
import argparse
import os

from agent.gemini import GeminiClient, GeminiError
from agent.manual import load_manual
from agent.pipeline import run_pipeline
from agent.retrieval import Retriever
from agent.gemini import RateLimiter
from config.settings import settings

ap = argparse.ArgumentParser()
ap.add_argument("--no-llm", action="store_true")
a = ap.parse_args()
cfg = lambda n, d: getattr(settings, n, os.getenv(n, d))

path = cfg("MANUAL_PATH", "agent/knowledge/pdm_manual.md")
print(f"[1] 매뉴얼 읽기: {path}")
m = load_manual(path)
kinds = {k: sum(1 for c in m.chunks if c.kind == k) for k in ("feature", "cause", "step", "sla")}
print(f"    조각 {len(m.chunks)}개 {kinds} | LOTO {len(m.loto)}개 | 진동 시한 {m.sla} | RUL 시한 {m.rul_sla}")
print(f"    결함 유형 → SOP: {m.sop_by_kind}")

key = str(cfg("GEMINI_API_KEY", "")).strip()
model, emodel = str(cfg("GEMINI_MODEL", "gemini-3.5-flash-lite")), str(cfg("GEMINI_EMBED_MODEL", "gemini-embedding-001"))
client = None
if key and not a.no_llm:
    client = GeminiClient(key, model, emodel)
    print(f"[2] Gemini 연결 확인 (생성 모델 {model}, 임베딩 모델 {emodel})")
    try:
        print("    임베딩:", client.embed(["연결 확인"]).shape)
    except GeminiError as e:
        print("    임베딩 실패:", e, "\n    → 모델 이름(GEMINI_EMBED_MODEL)과 키를 확인하세요. 실패해도 키워드 검색으로 동작합니다.")
    try:
        print("    생성:", client.generate_json('JSON 객체 {"ok": true} 만 출력하세요.'))
    except GeminiError as e:
        print("    생성 실패:", e, "\n    → 404면 모델 이름(GEMINI_MODEL), 403/400이면 키, 429면 무료 한도를 확인하세요. 실패해도 템플릿으로 동작합니다.")
        client = None
else:
    print("[2] Gemini 사용 안 함 (키가 없거나 --no-llm) → 템플릿 경로로 확인합니다.")

r = Retriever(m, client, emodel if client else "none", os.path.join(os.path.dirname(os.path.abspath(path)), "embeddings_cache.json"))
r.warm_up()
print(f"[3] 검색 방식: {r.mode}")


def hist(n, start, end, z):
    return [{"health_index": start + (end - start) * i / (n - 1), "z_env_bpfo": z[0], "z_env_bpfi": z[1], "z_env_bsf": z[2],
             "rul_pred": 110 - 3.8 * i, "cycle": 200 + i} for i in range(n)]


samples = [
    ({"asset_id": "ims_bearing_1", "source": "ims_set2", "level": "critical", "cycle": 565, "time": "2026-11-20T10:00:00+00:00",
      "health_index": 29.2, "rul_pred": None, "defect_kind": "bpfo", "defect_label": "외륜(BPFO)", "message": ""}, hist(20, 100, 29.2, (47.0, 13.0, 4.3))),
    ({"asset_id": "ims_bearing_4", "source": "ims_set2", "level": "warning", "cycle": 704, "time": "2026-11-20T10:05:00+00:00",
      "health_index": 39.4, "rul_pred": None, "defect_kind": "mixed", "defect_label": "복합 의심 (BPFO·BPFI)", "message": ""}, hist(20, 100, 39.4, (11.0, 9.5, 1.0))),
    ({"asset_id": "engine_049", "source": "cmapss_fd001", "level": "critical", "cycle": 275, "time": "2026-11-20T10:10:00+00:00",
      "health_index": 29.5, "rul_pred": 36.9, "message": ""}, hist(20, 90, 31.1, (0, 0, 0))),
]
print("[4] 샘플 알림 3종으로 작업 지시서 생성")
lim = RateLimiter(int(cfg("GEMINI_MAX_RPM", 10)))
for alert, h in samples:
    wo = run_pipeline(alert, h, r, client, lim, client is not None, model)
    print("\n" + "=" * 70 + f"\n[{alert['asset_id']} · {alert['level']}] AI 사용: {wo['llm_used']}" + (f" | 대체 사유: {wo['fallback_reason']}" if wo["fallback_reason"] else ""))
    print(wo["text"])
