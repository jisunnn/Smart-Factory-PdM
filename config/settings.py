import os
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트 경로 찾기 (smart_Factory/)
BASE_DIR = Path(__file__).resolve().parent.parent

# 루트 폴더에 있는 .env 파일 불러오기
load_dotenv(os.path.join(BASE_DIR, ".env"))

class Settings:
    """프로젝트 전체의 환경 변수 및 설정 값을 관리하는 클래스"""
    
    # App
    APP_ENV: str = os.getenv("APP_ENV", "development")
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true" # .env에 있는 DEBUG 변수의 값을 소문자로 가져오고 true와 같은지 결과 참거짓으로 반환
    
    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_TOPIC_SENSOR_DATA: str = os.getenv("KAFKA_TOPIC_SENSOR_DATA", "factory.sensor.raw")
    
    # Database (TimescaleDB / PostgreSQL)
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "postgres")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: str = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "smart_factory")

    #python
    KAFKA_TOPIC_VIBRATION_RAW: str = os.getenv("KAFKA_TOPIC_VIBRATION_RAW", "factory.vibration.raw")
    KAFKA_TOPIC_CYCLE_RAW: str     = os.getenv("KAFKA_TOPIC_CYCLE_RAW", "factory.cycle.raw")
    KAFKA_TOPIC_RUL_PRED: str      = os.getenv("KAFKA_TOPIC_RUL_PRED", "factory.rul.pred")
    
    # 파이프라인 on/off
    ENABLE_VIRTUAL_DATA: bool = os.getenv("ENABLE_VIRTUAL_DATA", "true").lower() == "true"
    ENABLE_RUL_SERVICE: bool = os.getenv("ENABLE_RUL_SERVICE", "true").lower() == "true"
    # ML
    ML_ARTIFACT_DIR: Path = BASE_DIR / "ml" / "artifacts"
    RUL_MODEL_FILES: list = os.getenv("RUL_MODEL_FILES", "rul_cnn_bilstm_fd001.pt").split(",")

    # 리플레이용 데이터 폴더 (본인 경로로 .env 에서 지정)
    CMAPSS_DATA_DIR: str = os.getenv("CMAPSS_DATA_DIR", "C:/pdm_data/cmapss")
    IMS_DATA_DIR: str = os.getenv("IMS_DATA_DIR", "C:/pdm_data/ims/2nd_test")

    # 알림 기준 (Health Index)
    ALERT_WARN_HI: float = float(os.getenv("ALERT_WARN_HI", "60"))
    ALERT_CRIT_HI: float = float(os.getenv("ALERT_CRIT_HI", "30"))
    ALERT_HYSTERESIS: float = float(os.getenv("ALERT_HYSTERESIS", "5"))
    KAFKA_TOPIC_ALERTS: str = os.getenv("KAFKA_TOPIC_ALERTS", "factory.alerts")

    # 메모리 정리 기준 시간 (기준 시간 이상 데이터가 없는 엔진의 윈도우 버퍼와 알림 상태를 삭제)
    ENGINE_IDLE_PRUNE_SEC: int = int(os.getenv("ENGINE_IDLE_PRUNE_SEC", "3600"))
    
    #property : 함수인데 변수처럼 사용 가능하게
    @property
    def DATABASE_URL(self) -> str:
        """SQLAlchemy 및 asyncpg에서 사용할 수 있는 DB 연결 URL 생성"""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

# 전역에서 가져다 쓸 수 있도록 인스턴스 생성
settings = Settings()


