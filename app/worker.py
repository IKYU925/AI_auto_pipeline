"""큐 소비 워커. 메시지 하나를 LangGraph 워크플로 한 번으로 처리한다.

실행: python -m app.worker
"""
import logging
import os
import signal
import socket
import time

from redis.exceptions import ConnectionError as RedisConnectionError

from app.container import Container
from app.graph.workflow import recursion_limit

log = logging.getLogger("worker")


def process_message(c: Container, msg_id: str, payload: dict) -> dict:
    incident_id = payload["incident_id"]
    incident = payload["incident"]
    c.store.set_status(incident_id, "running")
    try:
        final = c.graph.invoke(
            {
                "incident_id": incident_id,
                "incident": incident,
                "fingerprint": payload["fingerprint"],
                "attempts": 0,
                "history": [],
            },
            config={"recursion_limit": recursion_limit(c.settings.max_fix_attempts)},
        )
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 DLQ로 보내고 다음 메시지를 처리한다
        log.exception("incident %s failed", incident_id)
        c.store.set_status(incident_id, "failed", error=str(e))
        c.queue.dead_letter(msg_id, payload, repr(e))
        c.notifier.post(incident["channel"], f"자동 분석 중 오류가 발생했습니다: {e}", incident.get("thread_ts"))
        return {"outcome": "failed", "error": str(e)}

    c.store.set_status(
        incident_id,
        final["outcome"],
        report=final["report"],
        attempts=final.get("attempts", 0),
        cost=final.get("cost", {}),
        patch_path=final.get("patch_path", ""),
        classification=final.get("classification", {}),
        patch=final.get("fix", {}).get("patch", ""),
        reused_fix=bool(final.get("reused_fix")),
        test_selection=final.get("test_selection", {}),
        test_result={k: v for k, v in final.get("test_result", {}).items() if k != "failure_log"},
    )
    c.notifier.post(incident["channel"], final["report"], incident.get("thread_ts"))
    c.queue.ack(msg_id)
    return final


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    c = Container()
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("worker %s started", consumer)

    while running:
        try:
            c.queue.ensure_group()
            for msg_id, payload in c.queue.reclaim_stale(consumer):
                process_message(c, msg_id, payload)
            for msg_id, payload in c.queue.consume(consumer, count=1, block_ms=5000):
                process_message(c, msg_id, payload)
        except RedisConnectionError:
            # Redis 재시작 중이면 잠시 기다렸다가 다시 붙는다. 처리 중이던 메시지는 pending으로 남아 재처리된다
            log.warning("redis(%s) 연결 실패, 3초 후 재시도", c.settings.redis_url)
            time.sleep(3)


if __name__ == "__main__":
    run_forever()
