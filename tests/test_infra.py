import inspect

from app.core.queue import IncidentQueue
from app.infra import make_redis


def test_redis_socket_timeout_outlasts_queue_blocking_read():
    block_ms = inspect.signature(IncidentQueue.consume).parameters["block_ms"].default
    client = make_redis("redis://localhost:6379/0")
    assert client.connection_pool.connection_kwargs["socket_timeout"] > block_ms / 1000
