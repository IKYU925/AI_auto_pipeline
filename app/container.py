"""의존성 조립. API와 워커가 같은 구성 요소를 공유한다."""
from collections.abc import Callable
from functools import cached_property
from typing import Any

from app.config import Settings, get_settings
from app.core.queue import IncidentQueue
from app.core.store import IncidentStore
from app.infra import make_qdrant, make_redis
from app.llm.cost import CostTracker
from app.llm.router import LLMRouter, default_model_factory
from app.notify.slack import SlackNotifier
from app.rag.embedder import CachedEmbedder, build_embedder
from app.rag.ingest import IncrementalIngestor
from app.rag.retriever import Retriever


class Container:
    def __init__(self, settings: Settings | None = None, model_factory: Callable[[str], Any] | None = None):
        self.settings = settings or get_settings()
        self.model_factory = model_factory or default_model_factory

    @cached_property
    def redis(self):
        return make_redis(self.settings.redis_url)

    @cached_property
    def qdrant(self):
        return make_qdrant(self.settings.qdrant_url)

    @cached_property
    def queue(self) -> IncidentQueue:
        s = self.settings
        return IncidentQueue(self.redis, s.queue_stream, s.queue_group, s.queue_dlq_stream)

    @cached_property
    def store(self) -> IncidentStore:
        return IncidentStore(self.redis)

    @cached_property
    def embedder(self) -> CachedEmbedder:
        return build_embedder(self.settings.embedding_provider, self.settings.embedding_model, self.redis)

    @cached_property
    def ingestor(self) -> IncrementalIngestor:
        return IncrementalIngestor(self.qdrant, self.settings.qdrant_collection, self.embedder, self.redis)

    @cached_property
    def retriever(self) -> Retriever:
        s = self.settings
        return Retriever(self.qdrant, s.qdrant_collection, self.embedder, self.redis, s.retrieval_cache_ttl_sec)

    @cached_property
    def cost(self) -> CostTracker:
        return CostTracker(self.redis, self.settings.model_prices, self.settings.incident_budget_usd)

    @cached_property
    def router(self) -> LLMRouter:
        s = self.settings
        return LLMRouter(
            s.light_model, s.heavy_model, self.redis, self.cost, s.llm_cache_ttl_sec, self.model_factory,
            cache_enabled=s.opt_llm_cache, routing_enabled=s.opt_model_routing,
        )

    @cached_property
    def notifier(self) -> SlackNotifier:
        return SlackNotifier(self.settings.slack_bot_token)

    @cached_property
    def graph(self):
        from app.graph.nodes import Nodes
        from app.graph.workflow import build_graph

        nodes = Nodes(self.settings, self.router, self.retriever, self.cost, self.redis)
        return build_graph(nodes, self.settings.max_fix_attempts, early_exit=self.settings.opt_early_exit)
