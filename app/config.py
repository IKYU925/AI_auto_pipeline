"""환경변수 기반 설정. .env 파일 또는 컨테이너 환경변수로 주입한다."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 인입(n8n -> FastAPI) ---
    automation_token: str = "change-me"          # n8n이 X-Automation-Token 헤더로 보내는 공유 비밀값
    dedupe_ttl_sec: int = 600                    # 같은 에러 지문이 이 시간 안에 다시 오면 새 작업을 만들지 않는다

    # --- 메시지 큐 (Redis Streams) ---
    redis_url: str = "redis://localhost:6379/0"  # fakeredis:// 를 주면 메모리 Redis로 동작(데모, 테스트)
    queue_stream: str = "incidents"
    queue_group: str = "ai-workers"
    queue_dlq_stream: str = "incidents:dlq"

    # --- Vector DB (Qdrant) ---
    qdrant_url: str = "http://localhost:6333"    # :memory: 를 주면 프로세스 내 Qdrant로 동작
    qdrant_collection: str = "knowledge"

    # --- 임베딩 ---
    embedding_provider: str = "fastembed"        # fastembed(로컬, 무료) | openai | hash(테스트용)
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 한국어 문서 지원, 384차원

    # --- LLM 모델 분리 ---
    light_model: str = "anthropic:claude-haiku-5-5"   # 분류, 요약 같은 가벼운 작업
    heavy_model: str = "anthropic:claude-opus-5-5"    # 원인 분석, 수정 코드 생성
    # 모델별 100만 토큰당 단가(USD) [입력, 출력]. Anthropic 요금표 기준(2026-11-14 확인)
    model_prices: dict[str, list[float]] = Field(
        default_factory=lambda: {
            "anthropic:claude-haiku-5-5": [0.10, 0.50],
            "anthropic:claude-opus-5-5": [4.0, 20.0],
        }
    )

    # --- 캐시, 비용 ---
    llm_cache_ttl_sec: int = 86400
    retrieval_cache_ttl_sec: int = 600
    incident_budget_usd: float = 1.0             # 장애 1건당 LLM 비용 상한. 넘으면 사람에게 넘긴다

    # --- RAG 검색 ---
    retrieval_top_k: int = 5
    retrieval_lookback_days: int = 180           # 날짜 메타데이터로 먼저 범위를 줄인다
    context_chunk_max_chars: int = 1200          # 프롬프트에 넣는 청크 길이 상한(토큰 절감)

    # --- 수정안 검증(회귀테스트) ---
    target_repo_path: str = "./sample_repo"
    max_fix_attempts: int = 3
    test_timeout_sec: int = 300
    fallback_full_suite: bool = True             # 선별된 테스트가 0개면 전체 테스트로 대체
    failure_log_max_chars: int = 4000            # 재시도 때 LLM에 다시 보내는 실패 로그 상한
    artifacts_dir: str = "./artifacts"

    # --- Slack 결과 회신 ---
    slack_bot_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
