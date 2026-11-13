"""환경변수 설정. PG_API_KEY가 없으면 기동하지 않는다."""
import os
from dataclasses import dataclass


class MissingSetting(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    pg_url: str
    pg_api_key: str
    pg_timeout_sec: float
    tax_rate: float
    page_size: int
    slack_webhook_url: str


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise MissingSetting(f"{name} is not set")
    return value


def load_settings() -> Settings:
    return Settings(
        pg_url=os.environ.get("PG_URL", "http://fake-pg:8200"),
        pg_api_key=_required("PG_API_KEY"),
        pg_timeout_sec=float(os.environ.get("PG_TIMEOUT_SEC", "3")),
        tax_rate=float(os.environ.get("TAX_RATE", "0.1")),
        page_size=int(os.environ.get("PAGE_SIZE", "5")),
        slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL", ""),
    )
