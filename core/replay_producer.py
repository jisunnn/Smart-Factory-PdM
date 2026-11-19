"""실제 데이터셋(C-MAPSS, IMS)을 Kafka로 리플레이하는 프로듀서.

  python -m core.replay_producer cycle --data-dir /path/to/cmapss --interval 0.2
  python -m core.replay_producer vibration --data-dir /path/to/ims/2nd_test --interval 0.05
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from aiokafka import AIOKafkaProducer

from config.settings import settings

logger = logging.getLogger(__name__)

_COLS = ["unit", "cycle"] + [f"op{i}" for i in range(1, 4)] + [f"s{i}" for i in range(1, 22)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def iter_cycle_messages(data_dir, subset: str = "FD001"):
    """C-MAPSS test 데이터를 엔진들이 '동시에 가동 중'인 것처럼 사이클 순서로 섞어서 내보낸다."""
    d = Path(data_dir)
    test = pd.read_csv(d / f"test_{subset}.txt", sep=r"\s+", header=None, names=_COLS)
    rul_final = pd.read_csv(d / f"RUL_{subset}.txt", sep=r"\s+", header=None)[0].tolist()

    engines = {}
    for unit, g in test.groupby("unit"):
        engines[unit] = g.sort_values("cycle").to_dict("records")

    for step in range(max(len(v) for v in engines.values())):
        for unit, rows in engines.items():
            if step >= len(rows):
                continue
            r = rows[step]
            n = len(rows)
            yield {
                "asset_id": f"engine_{int(unit):03d}",
                "source": f"cmapss_{subset.lower()}",
                "cycle": int(r["cycle"]),
                "ts": _now(),
                "ops": {k: float(r[k]) for k in ("op1", "op2", "op3")},
                "sensors": {f"s{i}": float(r[f"s{i}"]) for i in range(1, 22)},
                # 데모 비교용 정답. 모델 입력으로는 절대 쓰지 않는다(추론기는 sensors만 읽음).
                "rul_true": int(rul_final[int(unit) - 1] + (n - int(r["cycle"]))),
            }


def _ims_file_time(name: str) -> str:
    y, mo, d, h, mi, s = map(int, re.findall(r"\d+", name)[:6])
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat()


def iter_vibration_messages(data_dir, channel: int = 0, chunk: int = 2048,
                            asset_id: str = "ims_bearing_1", fs: int = 20480):
    """IMS 파일(1초, 20,480샘플)을 chunk 샘플씩 잘라 내보낸다. 한 메시지가 Kafka 1MB 한도를 넘지 않게 함."""
    files = sorted(p for p in Path(data_dir).iterdir() if p.is_file())
    for p in files:
        x = pd.read_csv(p, sep="\t", header=None).values[:, channel]
        n_chunks = (len(x) + chunk - 1) // chunk
        ts = _ims_file_time(p.name)
        for i in range(n_chunks):
            seg = x[i * chunk:(i + 1) * chunk]
            yield {
                "asset_id": asset_id, "source": "ims_set2", "file": p.name, "ts": ts,
                "fs": fs, "channel": channel, "chunk_idx": i, "n_chunks": n_chunks,
                "samples": [round(float(v), 5) for v in seg],
            }


def _ims_dt(name: str) -> datetime:
    y, mo, d, h, mi, s = map(int, re.findall(r"\d+", name)[:6])
    return datetime(y, mo, d, h, mi, s)


def iter_ims_waveforms(data_dir, fs: int = 20480):
    """IMS 파일 1개(1초, 20,480샘플 x 4채널)를 베어링 1~4 메시지 4개로 나눠 파일 순서대로 내보낸다.
    메시지 1개 ≈ 150KB (Kafka 기본 한도 1MB 이내). 샘플은 원본 값 그대로(반올림 없음)."""
    files = sorted(p for p in Path(data_dir).iterdir() if p.is_file())
    if not files:
        raise ValueError(f"IMS 파일이 없습니다: {data_dir}")
    t0 = _ims_dt(files[0].name)
    for i, p in enumerate(files):
        arr = pd.read_csv(p, sep="\t", header=None).values
        elapsed = (_ims_dt(p.name) - t0).total_seconds() / 3600
        for ch in range(arr.shape[1]):
            yield {
                "asset_id": f"ims_bearing_{ch + 1}", "bearing": ch + 1, "source": "ims_set2",
                "file": p.name, "file_idx": i, "elapsed_h": elapsed, "ts": _now(), "fs": fs,
                "samples": arr[:, ch].tolist(),
            }


# 서비스 시연용 CWRU 파일: 모델은 0HP 로 학습했으므로 학습에 쓰지 않은 3HP(약 1730rpm) 파일을 재생한다.
CWRU_REPLAY_FILES = {100: ("normal", 0.0, 3), 108: ("inner", 0.007, 3), 121: ("ball", 0.007, 3), 133: ("outer", 0.007, 3),
                     212: ("inner", 0.021, 3), 225: ("ball", 0.021, 3), 237: ("outer", 0.021, 3)}
CWRU_FS, CWRU_WINDOW, CWRU_HOP = 12_000, 2048, 1024


def _load_cwru(path):
    """노트북 load_cwru 와 동일: Drive End 가속도(12kHz)와 회전수."""
    from scipy.io import loadmat
    m = loadmat(path)
    key = [k for k in m if k.endswith("_DE_time")][0]
    rpm_key = [k for k in m if k.endswith("RPM")]
    rpm = float(m[rpm_key[0]].ravel()[0]) if rpm_key else 1797.0
    return m[key].ravel().astype(float), rpm


def count_cwru_windows(data_dir, files=None, window=CWRU_WINDOW, hop=CWRU_HOP) -> int:
    total = 0
    for num in (files or CWRU_REPLAY_FILES):
        path = Path(data_dir) / f"{num}.mat"
        if path.exists():
            total += (len(_load_cwru(path)[0]) - window) // hop + 1
    return total


def iter_cwru_windows(data_dir, files=None, window=CWRU_WINDOW, hop=CWRU_HOP):
    """CWRU 파일을 2048샘플 윈도우(50% 겹침)로 잘라 파일 순서대로 내보낸다. 없는 파일은 건너뛴다.
    label_true 는 데모에서 정답과 비교하기 위한 값이며 분류기 입력으로는 쓰이지 않는다."""
    files = files or CWRU_REPLAY_FILES
    for num, (label, size, load) in files.items():
        path = Path(data_dir) / f"{num}.mat"
        if not path.exists():
            logger.warning("CWRU 파일 없음, 건너뜀: %s", path)
            continue
        x, rpm = _load_cwru(path)
        for pos in range(0, len(x) - window + 1, hop):
            yield {"asset_id": f"cwru_{num}", "source": "cwru_de12k", "file_id": num, "label_true": label,
                   "severity": size, "load_hp": load, "rpm": rpm, "win_idx": pos // hop, "fs": CWRU_FS,
                   "ts": _now(), "samples": x[pos:pos + window].tolist()}


async def replay(kind: str, data_dir: str, interval: float, loop_forever: bool):
    topic = {"cycle": settings.KAFKA_TOPIC_CYCLE_RAW, "vibration": settings.KAFKA_TOPIC_VIBRATION_RAW,
             "cwru": getattr(settings, "KAFKA_TOPIC_CWRU_RAW", "factory.cwru.raw")}[kind]
    gen_fn = {"cycle": iter_cycle_messages, "vibration": iter_ims_waveforms, "cwru": iter_cwru_windows}[kind]

    producer = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        key_serializer=lambda k: k.encode("utf-8"),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    logger.info("리플레이 시작: kind=%s topic=%s", kind, topic)
    try:
        while True:
            sent = 0
            for msg in gen_fn(data_dir):
                await producer.send_and_wait(topic, value=msg, key=msg["asset_id"])
                sent += 1
                await asyncio.sleep(interval)
            logger.info("한 바퀴 완료: %d건", sent)
            if not loop_forever:
                break
    finally:
        await producer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["cycle", "vibration", "cwru"])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--interval", type=float, default=0.2, help="메시지 간 대기(초)")
    ap.add_argument("--loop", action="store_true", help="끝나면 처음부터 반복")
    a = ap.parse_args()
    asyncio.run(replay(a.kind, a.data_dir, a.interval, a.loop))
