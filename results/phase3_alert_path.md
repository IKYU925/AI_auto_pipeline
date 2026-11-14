# 3단계 알림 경로 첫 실제 사례(PodOOMKilled)

2026-11-14 KST 21시 25분. 사람이 넣은 알림이 아니라 클러스터에서 실제로 난 장애가 알림으로 감지돼 자동화까지 이어진 첫 사례다.

## 경위

1. 런북 검색을 확인하려고 api 컨테이너 안에서 임베딩 모델을 올리는 프로세스를 하나 더 실행했다. api 프로세스는 직전의 지식 문서 수집으로 이미 모델을 올린 상태였다. 그래서 컨테이너가 메모리 limit(768Mi)을 넘어 OOMKilled로 재시작됐다.
2. Prometheus의 `PodOOMKilled` 규칙(3-4에서 추가)이 firing이 됐다. Alertmanager는 지금 라우팅(aiops namespace 전체)에 따라 `ai-automation`으로 보냈다.
3. 자동화가 장애로 접수했다. `source=alertmanager`, `alertname=PodOOMKilled`이고, 대상 서비스는 `container` 라벨에서 꺼낸 `api`다.
4. 워커가 분류(Haiku 5.5) 1회 뒤 비코드 장애로 판단해 런북 안내로 끝냈다. 접수부터 완료까지 5초, $0.000286이었다.
5. #aiops-report에 카드가 올라갔다(`report_ts` 기록).

## 찾은 문제와 수정

- 분류는 `config`(신뢰도 0.40)였다. 그런데 Kubernetes 런북이 카테고리 `infra`에 묶여 있어서, OOM 런북 대신 기동 실패 런북(`k8s/crashloop.md`)이 안내됐다.
- Kubernetes 런북 4개의 카테고리를 `general`로 바꿨다. 이 런북들은 분류 결과가 아니라 알림 이름과 증상으로 찾아야 한다. `general`은 검색 필터가 항상 포함한다.
- 바꾼 뒤 클러스터 DB에서 검색을 다시 확인했다. 알림 5개 유형 모두 맞는 런북이 1위로 나왔다. 그중에는 OOM이 `config`로 분류된 경우도 있다.

## 남은 확인

- 코드 버그 알림(`ShopapiException`)이 Loki 스택트레이스와 함께 자동 수정까지 가는 경로. infra_local_set의 라우팅 정리 뒤에 확인한다.
- 지금 라우팅은 aiops, shop namespace 알림을 모두 자동화로 보낸다. 이번처럼 실제 장애라면 맞는 동작이다. 다만 기본 규칙(KubePodNotReady 등)까지 장애로 접수되므로 infra_local_set에서 알림 이름으로 좁힌다.
