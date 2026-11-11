"""외부 인프라(Redis, Qdrant) 클라이언트 생성."""
from qdrant_client import QdrantClient
from redis import Redis


def make_redis(url: str) -> Redis:
    if url.startswith("fakeredis://"):
        import fakeredis

        return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=False)
    return Redis.from_url(url, decode_responses=False)


def make_qdrant(url: str) -> QdrantClient:
    if url == ":memory:":
        return QdrantClient(location=":memory:")
    return QdrantClient(url=url)
