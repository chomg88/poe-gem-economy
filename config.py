import os
from dataclasses import dataclass


@dataclass
class Settings:
    # 기본 설정
    league: str = os.getenv("POE_LEAGUE", "Mirage")

    # 데이터베이스 (기본: 로컬 SQLite)
    db_url: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{os.path.join(os.path.dirname(__file__), 'poe_economy.db')}",
    )

    # 크롤링 간격(초) - 실제 스케줄링은 cron/systemd 등 외부에서 관리
    crawl_interval_seconds: int = int(os.getenv("CRAWL_INTERVAL", "3600"))

    # 슬랙 웹훅 (반드시 환경변수로만 설정, 코드에 직접 쓰지 말 것)
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")

    # 기본 가격 변동 임계값 (%) — 이 비율 이상 변동 시 알림
    default_threshold_percent: float = float(os.getenv("DEFAULT_THRESHOLD_PERCENT", "10"))

    # 기본 가격 변동 절대값 임계값 (카오스) — 이 값 이상 변동 시 알림
    # % 조건과 AND 로 동시에 만족해야 알림 발송
    default_threshold_chaos: float = float(os.getenv("DEFAULT_THRESHOLD_CHAOS", "10"))


settings = Settings()

