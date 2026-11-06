import asyncio
import json
import logging
from aiokafka import AIOKafkaConsumer
from config.settings import settings
from models.database import db_manager
from datetime import datetime

logger = logging.getLogger(__name__)

class SensorDataConsumer:
    """Kafka 토픽으로부터 센서 데이터를 비동기로 구독(Consume)하여 TimescaleDB에 적재하는 클래스"""
    
    def __init__(self):
        self.consumer: AIOKafkaConsumer | None = None
        self.bootstrap_servers = settings.KAFKA_BOOTSTRAP_SERVERS
        self.topic = settings.KAFKA_TOPIC_SENSOR_DATA
        self.group_id = "smart-factory-pdm-group" # 컨슈머 그룹 ID : 컨슈머 식별을 위한 구분 id

    async def start(self):
        """Kafka Consumer 연결 및 구독 시작"""
        self.consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            # 파이프라인으로 들어오는 바이트 데이터를 JSON 딕셔너리로 자동 역직렬화(lambda m : 람다 함수로 매개변수m / m.decode('utf-8') : 바이트를 json문자열로 변환/ json.loads(...) : 문자열을 딕셔너리로
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            # 오프셋이 없을 경우 가장 최신 메시지부터 읽기 시작
            auto_offset_reset="latest"
        )
        await self.consumer.start()
        logger.info(f"Kafka Consumer가 시작되었습니다. (Topic: {self.topic}, Group: {self.group_id})")

    async def stop(self):
        """Kafka Consumer 연결 종료"""
        if self.consumer:
            await self.consumer.stop()
            logger.info("Kafka Consumer가 종료되었습니다.")

    async def consume_loop(self):
        """Kafka 메시지를 실시간으로 수신하여 TimescaleDB에 비동기 배치/단건 적재하는 루프"""
        if not self.consumer:
            raise RuntimeError("Consumer가 시작되지 않았습니다. start()를 먼저 호출하세요.")

        if not db_manager.pool:
            raise RuntimeError("Database pool이 연결되어 있지 않습니다. db_manager.connect()를 먼저 수행하세요.")

        logger.info("센서 데이터 수신 및 DB 적재 루프를 시작합니다...")

        insert_query = """
        INSERT INTO sensor_readings (time, machine_id, sensor_type, value, status_code)
        VALUES ($1, $2, $3, $4, $5);
        """

        try:
            # Kafka 토픽 스트림을 비동기로 순회하며 메시지 수신
            async for msg in self.consumer:
                data = msg.value
                logger.info(f"Consumed <- Topic: [{msg.topic}] | Partition: {msg.partition} | Data: {data}")

                # 데이터 추출
                reading_time = datetime.fromisoformat(data.get("time"))
                machine_id = data.get("machine_id")
                sensor_type = data.get("sensor_type")
                value = data.get("value")
                status_code = data.get("status_code", 0)

                # TimescaleDB 커넥션 풀에서 커넥션을 하나 빌려와 비동기 INSERT 실행
                async with db_manager.pool.acquire() as connection:
                    try:
                        await connection.execute(
                            insert_query,
                            reading_time,
                            machine_id,
                            sensor_type,
                            value,
                            status_code
                        )
                        logger.debug(f"DB 적재 완료: {machine_id} - {sensor_type} ({value})")
                    except Exception as db_err:
                        logger.error(f"데이터베이스 적재 실패 (SQL 에러): {db_err}")

        except asyncio.CancelledError:
            logger.info("센서 데이터 수신 루프가 정상적으로 취소되었습니다.")
        except Exception as e:
            logger.error(f"메시지 소비 중 에러 발생: {e}")
