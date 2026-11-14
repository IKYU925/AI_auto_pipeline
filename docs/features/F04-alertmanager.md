# F04. Alertmanager 어댑터와 Loki 스택트레이스 조회

상태: 구현 중(클러스터 없이 확인할 수 있는 범위)
관련: plan.md 9-3, 9-4, 3-4~3-5

## 문제

3단계에서는 사람이 Slack에 붙여 넣은 알림이 아니라 모니터링이 감지한 알림으로 자동화가 시작해야 한다. Alertmanager는 알림을 묶음(웹훅)으로 보내고, 알림에는 스택트레이스가 없다. 지금 자동화의 입구는 `POST /api/v1/incidents` 하나라 이 묶음을 받을 수 없다.

## 목표와 비목표

- 목표: Alertmanager 웹훅을 받아 알림마다 장애로 접수한다. 접수 뒤의 경로(중복 제거, 큐, 워커, 회신)는 기존과 같다.
- 목표: 코드 버그 계열 알림은 Loki에서 해당 서비스의 최근 ERROR 로그를 찾아 스택트레이스로 붙인다.
- 목표: `resolved` 알림은 같은 알림으로 만든 장애에 해소 시각만 남긴다.
- 비목표: 알림 규칙(PrometheusRule, Loki ruler)과 Alertmanager 라우팅. 3-4에서 클러스터와 infra_local_set에서 한다.
- 비목표: Kubernetes 장애 런북(`knowledge/k8s/`). 실제 알림 라벨을 본 뒤 3-5에서 쓴다.

## 운영자 시나리오

1. shopapi에 버그가 배포돼 500이 난다. shopapi가 JSON ERROR 로그를 남긴다.
2. Loki ruler의 ShopapiException이 1분 안에 뜨고, Alertmanager가 30초 묶음 대기 뒤 자동화로 보낸다.
3. 자동화는 Loki에서 그 로그의 `exc_info`를 꺼내 스택트레이스로 붙이고 장애로 접수한다.
4. 분석과 검증을 마친 카드가 설정한 Slack 채널에 올라온다.
5. 수정이 배포되고 알림이 해소되면 장애 기록에 해소 시각이 남는다.

## 요구사항

- 입구: `POST /api/v1/alerts/alertmanager`. 인증은 `Authorization: Bearer <토큰>` 또는 기존 `X-Automation-Token`.
- 알림 종류 표(알림 이름 → 대상 라벨 순서, 스택트레이스 조회 여부)

| 알림 | 대상(먼저 있는 라벨) | Loki 조회 |
| --- | --- | --- |
| ShopapiException | service | 예 |
| ShopapiHighErrorRate | service | 예 |
| ShopapiSlowPayment | service | 아니오 |
| KubePodCrashLooping | container, pod | 아니오 |
| PodOOMKilled | container, pod | 아니오 |
| KubeNodeNotReady | node | 아니오 |
| 그 밖의 이름 | service, container, pod, node, namespace | 아니오 |

- `firing` 알림
  - 장애 정보: `source=alertmanager`, `alertname`, `alert_fingerprint`(Alertmanager의 `fingerprint`), `service`(위 표), `severity`(라벨), `message`(`[알림 이름] 요약` 주석), 회신 채널은 설정값.
  - 같은 `alert_fingerprint`의 장애가 아직 해소되지 않았으면 새로 만들지 않는다. Alertmanager는 1시간마다 같은 알림을 다시 보낸다.
  - 그 밖에는 기존과 같은 지문 중복 제거와 큐 적재를 탄다.
- `resolved` 알림: 같은 `alert_fingerprint`이고 해소 시각이 없는 장애에 `endsAt`을 `alert_resolved_at`으로 남긴다.
- Loki 조회: `startsAt` 5분 전부터 지금까지 `{namespace, service}` 스트림에서 `| json | level="ERROR"`로 가장 최근 한 줄을 찾아 `exc_info`를 쓴다. Loki가 없거나 실패하면 스택트레이스 없이 접수한다.
- 응답: 접수한 장애 ID, 해소 처리 수, 이미 열린 장애로 묶인 알림 수.

## 완료 기준

- [x] 실제 Alertmanager 웹훅 형식의 묶음(firing 2건, resolved 1건)을 받아 장애 2건 접수, 해소 1건 기록(`tests/test_alertmanager.py`)
- [x] 같은 알림이 다시 오면 새 장애를 만들지 않음(테스트)
- [x] ShopapiException은 Loki 응답의 `exc_info`가 장애 스택트레이스로 들어감, Loki 실패 시 스택트레이스 없이 접수(테스트, Loki는 가짜 HTTP)
- [x] Bearer 토큰과 기존 헤더 모두 인증, 틀린 토큰은 401(테스트)
- [ ] 클러스터에서 버그 주입부터 Slack 회신까지 사람 개입 없이 완주(3-5, 클러스터 필요)

## 설계 메모

- 알림 종류는 표(`ALERT_KINDS`) 하나로 둔다. 3-4에서 실제 라벨을 본 뒤 고칠 곳이 한 군데다.
- 접수 로직은 `POST /api/v1/incidents`와 같은 함수를 쓴다. 입구가 달라도 중복 제거와 큐 적재는 하나다.

## 열린 질문

- kube-state-metrics 기반 알림의 실제 라벨(service가 kube-state-metrics로 오는지)은 3-4에서 확인하고 표를 고친다.

## 변경 이력
