"""Pod 알림 시점의 컨테이너 상태와 K8s 이벤트를 분석 입력(텍스트)으로 만든다. 스택트레이스가 없는 인프라 장애의 근거다(4-3)."""
import logging

import httpx

log = logging.getLogger(__name__)

MAX_EVENTS = 10
MAX_MESSAGE = 300


def _state(name: str, s: dict) -> str:
    parts = [f"컨테이너 {name}"]
    if waiting := s.get("state", {}).get("waiting"):
        parts.append(f"대기 {waiting.get('reason')}: {waiting.get('message', '')[:MAX_MESSAGE]}".rstrip(": "))
    if s.get("restartCount"):
        parts.append(f"재시작 {s['restartCount']}회")
    if last := s.get("lastState", {}).get("terminated"):
        parts.append(f"직전 종료 {last.get('reason')}(exit {last.get('exitCode')})")
    return ", ".join(parts)


def read_pod_events(kube, namespace: str, pod: str) -> str | None:
    """조회에 실패해도 접수는 한다. 이 근거만 빠진다."""
    lines = []
    try:
        status = kube.get(f"/api/v1/namespaces/{namespace}/pods/{pod}").get("status", {})
        lines += [_state(s["name"], s) for s in status.get("containerStatuses", [])]
    except httpx.HTTPError as e:
        log.warning("pod %s/%s failed: %s", namespace, pod, e)
    try:
        items = kube.get(f"/api/v1/namespaces/{namespace}/events", fieldSelector=f"involvedObject.name={pod}")["items"]
    except httpx.HTTPError as e:
        log.warning("events of %s/%s failed: %s", namespace, pod, e)
        items = []
    items.sort(key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "", reverse=True)
    lines += [f"{e.get('type')} {e.get('reason')} x{e.get('count') or 1}: {(e.get('message') or '')[:MAX_MESSAGE]}"
              for e in items[:MAX_EVENTS]]
    return "\n".join(lines) or None
