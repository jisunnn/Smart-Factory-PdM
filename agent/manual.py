"""매뉴얼(Markdown) 파서. 매뉴얼의 구조(표, 절, 번호 목록)를 읽어 검색 가능한 조각(Chunk)과 표준 값(대응 시한, LOTO 등)으로 만든다.

시한·SOP 코드·조치 문장은 모두 여기서 읽은 매뉴얼 원문이며, LLM이 만들지 않는다.
매뉴얼 형식이 예상과 다르면 ManualError 로 알려 준다(조용히 잘못 읽지 않도록).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


class ManualError(Exception):
    pass


STAGE_LABEL = {"warning": "주의 단계", "critical": "긴급 단계", "common": "공통"}
LEVEL_KO = {"warning": "주의 (Warning)", "critical": "긴급 (Critical)"}


@dataclass
class Chunk:
    id: str
    sop: str            # SOP-BRG-01 ... / SOP-RUL / LOTO
    kind: str           # feature | cause | step | sla | loto
    stage: str | None   # warning | critical | common | None
    text: str
    ref: str            # 사람이 읽는 출처 (예: "3.1 외륜 결함 · 표준 조치 2")
    tag: str | None = None


@dataclass
class Manual:
    text: str
    sha: str
    chunks: list[Chunk] = field(default_factory=list)
    sla: dict = field(default_factory=dict)          # 진동 경로 level -> 대응 시한
    rul_sla: dict = field(default_factory=dict)      # RUL 경로 level -> 대응 시한
    loto: list[Chunk] = field(default_factory=list)
    sop_by_kind: dict = field(default_factory=dict)  # bpfo/bpfi/bsf/other/rul -> SOP 코드
    titles: dict = field(default_factory=dict)       # SOP 코드 -> 절 제목

    def steps_for(self, sop: str, level: str) -> list[Chunk]:
        """해당 경보 단계의 조치와 공통 조치를 매뉴얼 순서대로. (긴급일 때 이미 지난 주의 단계 조치는 제외)"""
        return [c for c in self.chunks if c.sop == sop and c.kind == "step" and c.stage in (level, "common")]

    def sop_for_kind(self, kind: str) -> str:
        return self.sop_by_kind.get(kind) or self.sop_by_kind["other"]

    def chunk_by_id(self, cid: str) -> Chunk | None:
        return next((c for c in self.chunks if c.id == cid), None)


def _clean(s: str) -> str:
    s = re.sub(r"\*\([^)]*\)\*", "", s)           # *( ... )* 이탤릭 부연 설명 제거
    s = re.sub(r"\*{1,2}", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([.,])", r"\1", s)            # 제거한 부연 설명 앞에 남은 공백 정리


def _split_sections(text: str) -> dict:
    """'## 2.' / '### 2.1.' 형태 제목 -> 본문 줄 목록. 키는 '2.1', '3.4', '4' 처럼 번호."""
    secs, key, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^(#{2,3})\s+(\d+(?:\.\d+)?)\.?\s*(.*)$", line)
        if m:
            if key:
                secs[key] = (title, buf)
            key, title, buf = m.group(2), m.group(3).strip(), []
        elif line.startswith("#") and key:           # 번호 없는 제목(예: '### [LangGraph...]')도 앞 절의 본문으로 둔다
            buf.append(line)
        elif key:
            buf.append(line)
    if key:
        secs[key] = (title, buf)
    return secs


def _table_rows(lines):
    for ln in lines:
        if ln.strip().startswith("|"):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            yield cells


def load_manual(path) -> Manual:
    text = open(path, encoding="utf-8").read()
    m = Manual(text=text, sha=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])
    secs = _split_sections(text)

    # ---- 3.1 ~ 3.4: 결함별 SOP ----
    for num in ("3.1", "3.2", "3.3", "3.4"):
        if num not in secs:
            raise ManualError(f"매뉴얼에 {num} 절이 없습니다.")
        title, lines = secs[num]
        joined = title + "\n" + "\n".join(lines)
        code = re.search(r"SOP-BRG-\d{2}", joined)
        if not code:
            raise ManualError(f"{num} 절에서 SOP 코드를 찾지 못했습니다.")
        sop = code.group(0)
        short = re.sub(r"\s*\([A-Za-z][^)]*\)\s*$", "", title).strip()
        m.titles[sop] = f"{num} {short}"
        in_steps, n_steps = False, 0
        for ln in lines:
            f = re.match(r"^\s*\*\s+\*\*(진단 특징|주요 원인)\s*:?\s*\*{0,2}\s*(.*)$", ln)   # 닫히지 않은 ** 도 허용
            if f:
                kind = "feature" if f.group(1) == "진단 특징" else "cause"
                m.chunks.append(Chunk(f"{sop}-{kind}", sop, kind, None, _clean(f.group(2)), f"{num} {short} · {f.group(1)}"))
                in_steps = False
                continue
            if re.match(r"^\s*\*\s+\*\*표준 조치 절차", ln):
                in_steps = True
                continue
            s = re.match(r"^\s+(\d+)\.\s+(.*)$", ln)
            if in_steps and s:
                n_steps += 1
                body = s.group(2).strip()
                t = re.match(r"^\*\*\[(.+?)\]\*\*\s*(.*)$", body)
                tag, content = (t.group(1), t.group(2)) if t else (None, body)
                stage = {"주의 단계": "warning", "긴급 단계": "critical"}.get(tag or "", "common")
                m.chunks.append(Chunk(f"{sop}-S{s.group(1)}", sop, "step", stage, _clean(content),
                                      f"{num} {short} · 표준 조치 {s.group(1)}", tag))
        if n_steps == 0:
            raise ManualError(f"{num} 절에서 표준 조치 단계를 찾지 못했습니다.")

    # ---- 2.1: 진동 경로 대응 시한 ----
    if "2.1" not in secs:
        raise ManualError("매뉴얼에 2.1 절이 없습니다.")
    for cells in _table_rows(secs["2.1"][1]):
        head = _clean(cells[0]) if cells else ""
        if "주의" in head and "Warning" in head:
            m.sla["warning"] = _clean(cells[-1])
        elif "긴급" in head and "Critical" in head:
            m.sla["critical"] = _clean(cells[-1])
    if set(m.sla) != {"warning", "critical"}:
        raise ManualError("2.1 절 표에서 주의/긴급 대응 시한을 읽지 못했습니다.")
    for lv, t in m.sla.items():
        m.chunks.append(Chunk(f"SLA-{lv}", "SOP-BRG", "sla", lv, t, f"2.1 진동 기반 설비 상태 등급 · {LEVEL_KO[lv]}"))

    # ---- 2.2: RUL 경로 조치 ----
    if "2.2" not in secs:
        raise ManualError("매뉴얼에 2.2 절이 없습니다.")
    n = 0
    for cells in _table_rows(secs["2.2"][1]):
        head = _clean(cells[0]) if cells else ""
        lv = "warning" if ("주의" in head and "Warning" in head) else "critical" if ("긴급" in head and "Critical" in head) else None
        if lv:
            n += 1
            m.chunks.append(Chunk(f"SOP-RUL-{lv}", "SOP-RUL", "step", lv, _clean(cells[-1]), f"2.2 RUL 판정 기준 · {LEVEL_KO[lv]}"))
            m.rul_sla[lv] = {"critical": "당해 운항/가동 사이클 종료 즉시", "warning": "계획 정비(Overhaul) 일정 확정 시"}[lv]
    if n != 2:
        raise ManualError("2.2 절 표에서 RUL 주의/긴급 조치를 읽지 못했습니다.")

    # ---- 4장: LOTO, 결함 유형 -> SOP 대응표 ----
    if "4" not in secs:
        raise ManualError("매뉴얼에 4장이 없습니다.")
    body = secs["4"][1]
    for ln in body:
        if ln.startswith("#"):
            break
        s = re.match(r"^(\d+)\.\s+(.*)$", ln)
        if s:
            m.loto.append(Chunk(f"LOTO-{s.group(1)}", "LOTO", "loto", "critical", _clean(s.group(2)), f"4 현장 작업 안전 수칙 · {s.group(1)}"))
    if len(m.loto) < 3:
        raise ManualError("4장에서 LOTO 절차(3개)를 읽지 못했습니다.")
    for cells in _table_rows(body):
        if len(cells) >= 2 and re.match(r"^SOP-[A-Z]+(-\d+)?$", _clean(cells[1])):
            label, code = _clean(cells[0]), _clean(cells[1])
            for key, kind in (("BPFO", "bpfo"), ("BPFI", "bpfi"), ("BSF", "bsf"), ("복합", "other"), ("RUL", "rul")):
                if key in label:
                    m.sop_by_kind[kind] = code
    missing = {"bpfo", "bpfi", "bsf", "other", "rul"} - set(m.sop_by_kind)
    if missing:
        raise ManualError(f"결함 유형 → SOP 코드 대응표에서 읽지 못한 항목: {sorted(missing)}")
    for kind in ("bpfo", "bpfi", "bsf", "other"):
        sop = m.sop_by_kind[kind]
        for lv in ("warning", "critical"):
            if not m.steps_for(sop, lv):
                raise ManualError(f"{sop} 에 {lv} 단계 조치가 없습니다.")
    return m
