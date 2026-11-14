"""자동화의 실시간 지표(api `/metrics`). 장애 결과와 LLM 비용 같은 이력 지표는 Grafana가 PostgreSQL에서 직접 읽는다."""
from prometheus_client import CollectorRegistry, Counter, generate_latest
from prometheus_client.core import GaugeMetricFamily

from app.core.queue import IncidentQueue

QUEUE_GAUGES = {
    "waiting": "접수됐지만 워커가 아직 가져가지 않은 장애 수",
    "in_progress": "워커가 가져가 처리 중인(ack 전) 장애 수",
    "dead_letters": "처리에 실패해 DLQ로 간 누적 장애 수",
}


class QueueCollector:
    """큐 길이는 수집할 때 Redis에서 읽는다. api와 워커가 따로 세면 재시작 때 값이 어긋난다."""

    def __init__(self, queue: IncidentQueue):
        self.queue = queue

    def collect(self):
        depth = self.queue.depth()
        for key, doc in QUEUE_GAUGES.items():
            yield GaugeMetricFamily(f"aiops_queue_{key}", doc, value=depth[key])


class Metrics:
    def __init__(self, queue: IncidentQueue):
        self.registry = CollectorRegistry()
        self.received = Counter("aiops_incidents_received", "장애 접수 요청 수", ["source", "deduplicated"],
                                registry=self.registry)
        self.registry.register(QueueCollector(queue))

    def render(self) -> bytes:
        return generate_latest(self.registry)
