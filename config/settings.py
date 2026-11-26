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

    #Kafka Topic
    KAFKA_TOPIC_VIBRATION_RAW: str = os.getenv("KAFKA_TOPIC_VIBRATION_RAW", "factory.vibration.raw")
    KAFKA_TOPIC_CYCLE_RAW: str = os.getenv("KAFKA_TOPIC_CYCLE_RAW", "factory.cycle.raw")
    KAFKA_TOPIC_RUL_PRED: str = os.getenv("KAFKA_TOPIC_RUL_PRED", "factory.rul.pred")
    KAFKA_TOPIC_CWRU_RAW: str = os.getenv("KAFKA_TOPIC_CWRU_RAW", "factory.cwru.raw")
    
    # 파이프라인 on/off
    ENABLE_VIRTUAL_DATA: bool = os.getenv("ENABLE_VIRTUAL_DATA", "true").lower() == "true"
    ENABLE_RUL_SERVICE: bool = os.getenv("ENABLE_RUL_SERVICE", "true").lower() == "true"
    ENABLE_VIB_SERVICE: bool = os.getenv("ENABLE_VIB_SERVICE", "true").lower() == "true"
    ENABLE_CWRU_SERVICE: bool = os.getenv("ENABLE_CWRU_SERVICE", "true").lower() == "true"
    ENABLE_AGENT_SERVICE: bool = os.getenv("ENABLE_AGENT_SERVICE", "true").lower() == "true"

    # ML
    ML_ARTIFACT_DIR: Path = BASE_DIR / "ml" / "artifacts"
    RUL_MODEL_FILES: list = os.getenv("RUL_MODEL_FILES", "rul_cnn_bilstm_fd001.pt").split(",")

    # 리플레이용 데이터 폴더 (본인 경로로 .env 에서 지정)
    CMAPSS_DATA_DIR: str = os.getenv("CMAPSS_DATA_DIR", "C:/factory_pdm_data/cmapss")
    IMS_DATA_DIR: str = os.getenv("IMS_DATA_DIR", "C:/factory_pdm_data/2nd_test")
    CWRU_DATA_DIR: str = os.getenv("CWRU_DATA_DIR", "C:/factory_pdm_data/cwru")

    # 알림 기준 (Health Index)
    ALERT_WARN_HI: float = float(os.getenv("ALERT_WARN_HI", "60"))
    ALERT_CRIT_HI: float = float(os.getenv("ALERT_CRIT_HI", "30"))
    ALERT_HYSTERESIS: float = float(os.getenv("ALERT_HYSTERESIS", "5"))
    KAFKA_TOPIC_ALERTS: str = os.getenv("KAFKA_TOPIC_ALERTS", "factory.alerts")

    # 결함 기준 임계값 설정
    FAULT_MIN_Z: float = float(os.getenv("FAULT_MIN_Z", "3.0"))
    FAULT_MARGIN: float = float(os.getenv("FAULT_MARGIN", "1.5"))

    # 메모리 정리 기준 시간 (기준 시간 이상 데이터가 없는 엔진의 윈도우 버퍼와 알림 상태를 삭제)
    ENGINE_IDLE_PRUNE_SEC: int = int(os.getenv("ENGINE_IDLE_PRUNE_SEC", "3600"))

    # GEMINI and agent settings
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    GEMINI_EMBED_MODEL: str = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
    GEMINI_MAX_RPM: int = int(os.getenv("GEMINI_MAX_RPM", "10"))               # 분당 호출 제한
    AGENT_USE_LLM: bool = os.getenv("AGENT_USE_LLM", "true").lower() == "true"  # false 면 항상 템플릿
    AGENT_MAX_WAIT_SEC: float = float(os.getenv("AGENT_MAX_WAIT_SEC", "20"))

    # 메뉴얼 파일 경로
    MANUAL_PATH: str = os.getenv("MANUAL_PATH", str(BASE_DIR / "agent" / "knowledge" / "pdm_manual.md"))
    
    #property : 함수인데 변수처럼 사용 가능하게
    @property
    def DATABASE_URL(self) -> str:
        """SQLAlchemy 및 asyncpg에서 사용할 수 있는 DB 연결 URL 생성"""
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

# 전역에서 가져다 쓸 수 있도록 인스턴스 생성
settings = Settings()


    
    


