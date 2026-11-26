"""factory.rul.pred 를 처음부터 읽어 예측 건수와 엔진별 마지막 예측 오차를 출력한다.

사용: smart_Factory/ (프로젝트 루트)에 복사 → venv 활성화 → python check_predictions.py
"""
import asyncio
import json

import numpy as np
from aiokafka import AIOKafkaConsumer

from config.settings import settings

RUL_CAP = 125


async def main():
    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC_RUL_PRED,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=None,                      # 컨슈머 그룹을 만들지 않음 (오프셋 저장 안 함)
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    await consumer.start()
    total, last = 0, {}
    try:
        while True:
            batch = await consumer.getmany(timeout_ms=5000)   # 5초 동안 새 메시지가 없으면 종료
            if not batch:
                break
            for msgs in batch.values():
                for m in msgs:
                    total += 1
                    last[m.value["asset_id"]] = m.value        # 엔진별 마지막 예측만 유지
    finally:
        await consumer.stop()

    print(f"총 예측 건수: {total}  (기대값 10196) | 엔진 수: {len(last)}")
    pairs = [(v["rul_pred"], v["rul_true"]) for v in last.values() if v.get("rul_true") is not None]
    if not pairs:
        return
    pred = np.clip(np.array([p for p, _ in pairs], dtype=float), 0, RUL_CAP)
    true = np.array([t for _, t in pairs], dtype=float)
    rmse = lambda a, b: float(np.sqrt(np.mean((a - b) ** 2)))
    print(f"엔진별 마지막 예측 RMSE: 클리핑 정답 {rmse(pred, np.minimum(true, RUL_CAP)):.2f} | 원본 정답 {rmse(pred, true):.2f}")
    print("(노트북 test 평가: 5-시드 평균 12.98 / 14.54 — 단일 모델이라 비슷한 범위면 정상)")


if __name__ == "__main__":
    asyncio.run(main())