"""외부 인프라(Redis) 클라이언트 생성."""
from redis import Redis

REDIS_SOCKET_TIMEOUT_SEC = 30


def make_redis(url: str) -> Redis:
    if url.startswith("fakeredis://"):
        import fakeredis

        return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=False)
    # 워커는 XREADGROUP으로 5초씩 기다린다. redis-py 8의 기본 소켓 제한(5초)이면 대기 중에 TimeoutError가 난다
    return Redis.from_url(url, decode_responses=False, socket_timeout=REDIS_SOCKET_TIMEOUT_SEC)

