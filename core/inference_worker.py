"""factory.cycle.raw 를 구독해 RUL을 예측하고, factory.rul.pred 로 발행 + TimescaleDB(rul_predictions)에 저장.

  python -m core.inference_worker      (FastAPI 앱과 별도 프로세스로 실행)
"""
from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from config.settings import settings

logger = logging.getLogger(__name__)


def process_message(predictor, data: dict):
    """메시지 1건 → 예측 결과 dict 또는 None(윈도우가 아직 안 찼거나 처리 불가)."""
    try:
        out = predictor.update(data["asset_id"], data["sensors"], data.get("cycle"))
    except (KeyError, ValueError) as e:
        logger.warning("메시지 스킵: %s", e)
        return None
    if out is None:
        return None
    return {
        "asset_id": data["asset_id"],
        "source": data.get("source"),
        "cycle": data.get("cycle"),
        "ts": data.get("ts"),
        "rul_pred": round(out["rul"], 2),
        "health_index": round(out["health_index"], 1),
        "rul_true": data.get("rul_true"),     # 데모 비교용(있을 때만)
    }


async def handle_batch(batch, predictor, producer, db_pool, insert_fn, alert_engine=None, alert_insert_fn=None):
    """getmany 결과 한 묶음을 처리: 예측 → Kafka 발행 → (알림 판정) → DB 일괄 저장. 저장한 예측 건수를 반환."""
    rows, alerts = [], []
    for _tp, msgs in batch.items():
        for m in msgs:
            result = process_message(predictor, m.value)
            if result is None:
                continue
            await producer.send_and_wait(settings.KAFKA_TOPIC_RUL_PRED, value=result, key=result["asset_id"])
            rows.append(result)
            if alert_engine is not None:
                alert = alert_engine.evaluate(result["asset_id"], result.get("cycle"),
                                              result["health_index"], result["rul_pred"], result.get("ts"))
                if alert:
                    alerts.append(alert)
    if rows:
        await insert_fn(db_pool, rows)
    if alerts and alert_insert_fn is not None:
        await alert_insert_fn(db_pool, alerts)
        topic = getattr(settings, "KAFKA_TOPIC_ALERTS", "factory.alerts")
        for a in alerts:                                  # 후속 소비자(예: 알림 해설 에이전트)용 이벤트
            await producer.send_and_wait(topic, value=a, key=a["asset_id"])
        logger.warning("알림 %d건 발생: %s", len(alerts), "; ".join(a["message"] for a in alerts[:3]))
    return len(rows)


async def run():
    from ml.rul_model import RULPredictor          # torch import는 워커에서만
    from models.database import db_manager
    from models.rul_repository import init_rul_tables, insert_rul_predictions
    from models.alert_repository import init_alert_table, insert_alerts
    from core.alert_engine import AlertEngine

    predictor = RULPredictor(settings.ML_ARTIFACT_DIR, settings.RUL_MODEL_FILES)

    await db_manager.connect()
    await init_rul_tables(db_manager.pool)
    await init_alert_table(db_manager.pool)
    alert_engine = AlertEngine(getattr(settings, "ALERT_WARN_HI", 60.0), getattr(settings, "ALERT_CRIT_HI", 30.0),
                                 getattr(settings, "ALERT_HYSTERESIS", 5.0))

    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC_CYCLE_RAW,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id="rul-inference",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=False,                  # DB 저장에 성공한 뒤에만 수동 커밋
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        key_serializer=lambda k: k.encode("utf-8"),
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await consumer.start()
    await producer.start()
    logger.info("Inference worker 시작 (models=%d)", len(predictor.models))

    saved = 0
    try:
        while True:
            batch = await consumer.getmany(timeout_ms=1000, max_records=500)
            if not batch:
                continue
            n = await handle_batch(batch, predictor, producer, db_manager.pool, insert_rul_predictions,
                                   alert_engine, insert_alerts)
            await consumer.commit()
            saved += n
            if n:
                logger.info("저장 누계 %d건 (이번 배치 %d건)", saved, n)
    finally:
        await consumer.stop()
        await producer.stop()
        await db_manager.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
