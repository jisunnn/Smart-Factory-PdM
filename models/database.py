import asyncpg
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

class DatabaseManager:
    """TimescaleDB(PostgreSQL) 비동기 연결 및 초기화를 담당하는 매니저 클래스"""
    
    def __init__(self):
        self.pool: asyncpg.Pool | None = None

    async def connect(self):
        """애플리케이션 시작 시 호출되는 데이터베이스 연결 풀 생성 함수"""
        if self.pool is None:
            try:
                # asyncpg를 이용해 PostgreSQL 연결 풀 생성 (설정값 활용)
                self.pool = await asyncpg.create_pool(   #await : async def 에서만 사용가능한 명령어. 시간이 걸리는 작업 앞에 사용. 이 작업 처리시간동안 다른 작업하라는 것
                    user=settings.DB_USER,
                    password=settings.DB_PASSWORD,
                    database=settings.DB_NAME,
                    host=settings.DB_HOST,
                    port=settings.DB_PORT,
                    min_size=2,
                    max_size=10
                )
                logger.info("TimescaleDB 연결 풀이 성공적으로 생성되었습니다.")
            except Exception as e:
                logger.error(f"TimescaleDB 연결 실패: {e}")
                raise

    async def disconnect(self):
        """애플리케이션 종료 시 호출되는 연결 풀 해제 함수"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("TimescaleDB 연결 풀이 안전하게 종료되었습니다.")

    async def init_tables(self):
        """센서 데이터를 저장할 테이블 및 TimescaleDB Hypertable 생성"""
        if not self.pool:
            raise RuntimeError("Database pool이 초기화되지 않았습니다. connect()를 먼저 호출하세요.")

        # 1. 센서 데이터 저장을 위한 기본 관계형/시계열 통합 테이블 생성
        create_table_query = """
        CREATE TABLE IF NOT EXISTS sensor_readings (
            time TIMESTAMPTZ NOT NULL,
            machine_id VARCHAR(50) NOT NULL,
            sensor_type VARCHAR(50) NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            status_code INT DEFAULT 0
        );
        """

        # 2. TimescaleDB Extension 활성화 (설치되어 있다는 가정하에)
        enable_extension_query = "CREATE EXTENSION IF NOT EXISTS timescaledb;"

        # 3. 일반 테이블을 시계열 최적화 테이블(Hypertable)로 변환하는 쿼리
        # (이미 하이퍼테이블로 변환되어 있다면 에러 없이 넘어가지 않도록 처리하거나 최초 1회 수행)
        create_hypertable_query = """
        SELECT create_hypertable('sensor_readings', 'time', if_not_exists => TRUE);
        """

        async with self.pool.acquire() as connection:
            try:
                # 테이블 생성
                await connection.execute(create_table_query)
                logger.info("sensor_readings 테이블이 준비되었습니다.")

                # TimescaleDB 확장 기능 활성화 시도
                try:
                    await connection.execute(enable_extension_query)
                    logger.info("TimescaleDB extension이 활성화되었습니다.")
                except Exception as ext_err:
                    logger.warning(f"TimescaleDB extension 활성화 스킵 또는 권한 부족 (이미 활성화되었을 수 있음): {ext_err}")

                # Hypertable 변환 실행
                await connection.execute(create_hypertable_query)
                logger.info("sensor_readings 테이블이 TimescaleDB Hypertable로 성공적으로 변환되었습니다.")

            except Exception as e:
                logger.error(f"데이터베이스 테이블 초기화 중 에러 발생: {e}")
                raise

# 전역에서 가져다 쓸 수 있는 데이터베이스 매니저 인스턴스 (db연결은 하나만 필요해서 여기에 선언함)
db_manager = DatabaseManager()

