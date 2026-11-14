---
service: common
category: general
doc_type: runbook
updated_at: 2026-11-14
---
# 메모리 초과 종료(PodOOMKilled) 대응

## 증상

`PodOOMKilled` 알림이 뜬다. 컨테이너의 마지막 종료 사유가 `OOMKilled`이고 재시작 횟수가 오른다. 처리 중이던 요청은 끊긴다.

## 판단

컨테이너 메모리가 `resources.limits.memory`를 넘었다. 큰 요청 한 번에 튄 것인지, 사용량이 계속 올라 limit에 닿은 것인지(누수) 먼저 나눈다. 큰 요청 때문이면 코드보다 limit과 요청 크기 제한이 먼저다.

## 조치

1. 종료 사유를 확인한다. `kubectl -n <namespace> describe pod <pod>`에서 `Last State: Terminated, Reason: OOMKilled`를 본다.
2. Grafana에서 해당 컨테이너의 `container_memory_working_set_bytes` 추이를 본다. 계단처럼 튀면 큰 요청, 꾸준히 오르면 누수다.
3. 지금 limit을 확인한다. `kubectl -n <namespace> get deploy <name> -o jsonpath='{.spec.template.spec.containers[0].resources}'`
4. 일시 조치로 limit을 올리고 재배포한다. 노드 메모리 여유(`kubectl top nodes`) 안에서 올린다.
5. 큰 요청이 원인이면 요청 크기 제한을 서비스나 Ingress에 둔다. 누수면 담당자에게 넘긴다.

## 확인

재시작 횟수가 멈추고 메모리 사용량이 limit 아래에서 안정되면 해소다.
