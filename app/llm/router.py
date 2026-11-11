"""작업 난이도에 따른 모델 라우팅 + 응답 캐시 + 비용 기록.

- LIGHT: 에러 분류, 요약처럼 패턴 인식 위주의 작업 -> 싸고 빠른 모델
- HEAVY: 원인 추론, 수정 코드 생성 -> 고성능 모델

분류 결과는 에러 지문(fingerprint)을 캐시 키로 쓰기 때문에, 같은 에러가 반복되면 LLM을 다시 부르지 않는다.
"""
import hashlib
import json
import logging
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel
from redis import Redis

from app.llm.cost import CostTracker

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class Tier(str, Enum):
    LIGHT = "light"
    HEAVY = "heavy"


TASK_TIER: dict[str, Tier] = {
    "classify": Tier.LIGHT,
    "summarize": Tier.LIGHT,
    "root_cause": Tier.HEAVY,
    "generate_fix": Tier.HEAVY,
}

# 같은 입력이면 같은 답을 재사용해도 되는 작업만 캐시한다.
# generate_fix는 재시도마다 실패 로그가 달라지고, 새 답을 원하는 것이므로 캐시하지 않는다.
CACHEABLE_TASKS = {"classify", "summarize", "root_cause"}


def default_model_factory(name: str) -> Any:
    provider, _, model = name.partition(":")
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0, max_tokens=4096)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0)
    if provider == "fake":  # API 키 없이 실제 Redis, 워커 프로세스로 흐름만 점검할 때 (LIGHT_MODEL=fake:light)
        from app.llm.fake import FakeChatModel
        from scripts.demo_responder import demo_responder

        return FakeChatModel(name, demo_responder, [])
    raise ValueError(f"unsupported model provider: {name}")


class LLMRouter:
    def __init__(
        self,
        light_model: str,
        heavy_model: str,
        redis: Redis,
        cost: CostTracker,
        cache_ttl: int,
        model_factory: Callable[[str], Any] = default_model_factory,
    ):
        self.models = {Tier.LIGHT: light_model, Tier.HEAVY: heavy_model}
        self.redis = redis
        self.cost = cost
        self.cache_ttl = cache_ttl
        self.model_factory = model_factory
        self._clients: dict[str, Any] = {}

    def model_for(self, task: str) -> str:
        return self.models[TASK_TIER[task]]

    def _client(self, name: str) -> Any:
        if name not in self._clients:
            self._clients[name] = self.model_factory(name)
        return self._clients[name]

    def invoke(
        self,
        task: str,
        schema: type[T],
        system: str,
        user: str,
        incident_id: str,
        cache_key: str | None = None,
    ) -> T:
        model = self.model_for(task)
        key = None
        if task in CACHEABLE_TASKS:
            basis = cache_key or hashlib.sha256((system + "\n" + user).encode()).hexdigest()
            key = f"llm:{model}:{task}:{basis}"
            hit = self.redis.get(key)
            if hit:
                self.cost.record_cache_hit(incident_id, task)
                log.info("llm cache hit task=%s", task)
                return schema.model_validate_json(hit)

        self.cost.check_budget(incident_id)
        runnable = self._client(model).with_structured_output(schema, include_raw=True)
        out = runnable.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        parsed, raw = out["parsed"], out["raw"]
        if parsed is None:
            raise ValueError(f"LLM returned unparsable output for task={task}: {out.get('parsing_error')}")
        if isinstance(parsed, dict):
            parsed = schema.model_validate(parsed)

        usage = getattr(raw, "usage_metadata", None) or {}
        self.cost.record(incident_id, model, task, usage.get("input_tokens", 0), usage.get("output_tokens", 0))

        if key:
            self.redis.set(key, parsed.model_dump_json(), ex=self.cache_ttl)
        return parsed


def stable_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
