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


async def replay(kind: str, data_dir: str, interval: float, loop_forever: bool):
    topic = settings.KAFKA_TOPIC_CYCLE_RAW if kind == "cycle" else settings.KAFKA_TOPIC_VIBRATION_RAW
    gen_fn = iter_cycle_messages if kind == "cycle" else iter_vibration_messages

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
    ap.add_argument("kind", choices=["cycle", "vibration"])
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--interval", type=float, default=0.2, help="메시지 간 대기(초)")
    ap.add_argument("--loop", action="store_true", help="끝나면 처음부터 반복")
    a = ap.parse_args()
    asyncio.run(replay(a.kind, a.data_dir, a.interval, a.loop))
