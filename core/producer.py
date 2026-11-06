import asyncio
import json
import random
from datetime import datetime, timezone
from aiokafka import AIOKafkaProducer
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

class SensorDataProducer:
    """가상 센서 데이터를 생성하여 Kafka로 비동기 발행(Produce)하는 클래스"""
    
    def __init__(self):
        self.producer: AIOKafkaProducer | None = None # self.producer 인스턴스 변수가 AIOKafkaProducer 객체나 None을 값으로 담고 기본값은 None
        self.bootstrap_servers = settings.KAFKA_BOOTSTRAP_SERVERS
        self.topic = settings.KAFKA_TOPIC_SENSOR_DATA

    async def start(self):
        """Kafka Producer 연결 시작"""
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            # 파이썬 딕셔너리를 JSON 문자열 형태의 바이트(bytes)로 자동 직렬화
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        await self.producer.start()
        logger.info(f"Kafka Producer가 시작되었습니다. (Target: {self.bootstrap_servers})")

    async def stop(self):
        """Kafka Producer 연결 종료"""
        if self.producer:
            await self.producer.stop()
            logger.info("Kafka Producer가 종료되었습니다.")

    async def produce_loop(self, interval: float = 1.0):
        """주기적으로 가상 센서 데이터를 생성해 Kafka 토픽으로 전송하는 비동기 루프"""
        if not self.producer:
            raise RuntimeError("Producer가 시작되지 않았습니다. start()를 먼저 호출하세요.")

        machine_ids = ["CNC-MACHINE-01", "PRESS-MACHINE-02", "ROBOT-ARM-03"]
        sensor_types = ["vibration", "temperature", "rpm"]

        try:
            while True:
                # 1. 시뮬레이션용 가상 센서 데이터 생성
                now = datetime.now(timezone.utc).isoformat()
                machine_id = random.choice(machine_ids)
                sensor_type = random.choice(sensor_types)
                
                # 센서 종류별 정상/비정상 범위 시뮬레이션
                if sensor_type == "vibration":
                    value = round(random.uniform(0.1, 4.5), 3) # mm/s
                elif sensor_type == "temperature":
                    value = round(random.uniform(40.0, 95.0), 2) # °C
                else:
                    value = round(random.uniform(1150.0, 1250.0), 1) # RPM

                # 임계값 초과 시 status_code를 1(경고/이상)로 설정
                is_abnormal = (sensor_type == "temperature" and value > 85.0) or \
                              (sensor_type == "vibration" and value > 3.5)
                status_code = 1 if is_abnormal else 0

                payload = {
                    "time": now,
                    "machine_id": machine_id,
                    "sensor_type": sensor_type,
                    "value": value,
                    "status_code": status_code
                }

                # 2. Kafka 토픽으로 메시지 전송 (비동기 대기)
                await self.producer.send_and_wait(self.topic, payload) #지정했던 토픽 채널에 가상데이터 만든걸보낸다
                logger.info(f"Produced -> Topic: [{self.topic}] | Data: {payload}")

                # 3. 설정된 주기(초)만큼 대기 후 반복
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.info("센서 데이터 발행 루프가 정상적으로 취소되었습니다.")
        except Exception as e:
            logger.error(f"메시지 발행 중 에러 발생: {e}")

