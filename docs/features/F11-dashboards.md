# F11. 자동화 메트릭과 Grafana 대시보드

상태: 완료
관련: plan.md 9-5, 3-6

## 문제

자동화가 장애를 몇 건 받아 몇 건을 고쳤는지, 비용이 얼마인지, 큐가 밀렸는지 보려면 지금은 DB에 직접 질의해야 한다. 클러스터의 Grafana에는 자동화 지표가 없고, PostgreSQL 데이터 소스도 없다.

## 목표와 비목표

- 목표: 대시보드 2개. shopapi 상태(요청 수, 5xx 비율, p95 지연, Pod 재시작, 최근 에러 로그, 발생한 알림)와 자동화 현황(일별 장애 수와 결과, 자동 수정 성공률, 장애당 LLM 비용, 모델별 호출 비율, 큐 대기 수).
- 목표: 실시간 지표는 Prometheus, 이력 지표는 PostgreSQL에서 읽는다.
- 비목표: 고객사별 대시보드(3-9), 대시보드 알림.

## 운영자 시나리오

1. 장애가 몰리면 자동화 현황 대시보드에서 큐 대기 수가 오르는 것을 본다.
2. 하루가 끝나면 일별 장애 수, 결과별 비율, 장애당 비용을 한 화면에서 본다.
3. shopapi 대시보드에서 에러율이 오른 시점과 그때 뜬 알림, 에러 로그를 함께 본다.

## 요구사항

- api `/metrics`(인증 없음)
  - `aiops_incidents_received_total{source, deduplicated}`: 접수 요청 수. 중복으로 묶인 요청은 `deduplicated="true"`.
  - `aiops_queue_waiting`: 접수됐지만 워커가 아직 가져가지 않은 수.
  - `aiops_queue_in_progress`: 워커가 가져가 처리 중인(ack 전) 수.
  - `aiops_queue_dead_letters`: 처리에 실패해 DLQ로 간 누적 수.
- Grafana PostgreSQL 데이터 소스(uid `aiops-postgres`)는 읽기 전용 사용자 `grafana_ro`로 접속한다. `incidents`, `llm_calls`, `fixes`에 SELECT만 준다.
- 데이터 소스는 `scripts/k8s_grafana.sh`가 `monitoring` namespace에 `grafana_datasource` 라벨을 단 Secret으로 만든다. 비밀번호는 Git에 두지 않는다. 다시 실행해도 같은 상태가 된다.
- 대시보드 JSON은 `deploy/k8s/base/dashboards/`에 두고 `grafana_dashboard` 라벨을 단 ConfigMap으로 배포한다.

## 완료 기준

- [x] 접수 요청이 카운터에 `source`와 중복 여부로 잡히고, 큐 게이지가 대기, 처리 중, DLQ 수와 맞는다(테스트)
- [x] Prometheus가 ai-api를 수집한다(`up{job="ai-api"} == 1`)
- [x] `grafana_ro`는 SELECT만 되고 쓰기는 거부된다(클러스터에서 확인. DELETE는 read-only transaction, checkpoints 조회는 permission denied)
- [x] 두 대시보드의 모든 패널 질의가 Grafana API에서 오류 없이 결과를 돌려준다(`scripts/check_dashboards.py`)
- [x] 대시보드에서 장애 수, 성공률, 비용, 큐 길이가 보인다(`results/phase3_dashboards/`)

## 설계 메모

- 데이터 소스를 infra_local_set의 Grafana values 대신 이 저장소에서 만든다(plan.md 9-2의 계획과 다름). Grafana 데이터 소스 사이드카가 `monitoring` namespace의 라벨 붙은 Secret을 읽으므로 다른 저장소를 바꿀 필요가 없고, DB 비밀번호가 DB를 만든 저장소 밖으로 나가지 않는다.
- 큐 지표는 수집 시점에 Redis에서 읽는다. 카운터로 세면 api와 워커 양쪽을 고쳐야 하고 재시작 때 값이 어긋난다.
- 워커 쪽 지표(LLM 비용, 결과)는 이미 PostgreSQL에 남으므로 워커에 `/metrics`를 두지 않는다.

- `scripts/check_dashboards.py`는 Grafana 백엔드로 질의를 실행한다. 화면의 질의 방식 차이는 잡지 못한다. 통계 패널의 Prometheus instant 질의가 이 스크립트에서는 결과를 돌려줬지만 화면에서는 No data였다. 그래서 통계 패널도 range 질의로 바꿨고, 화면은 스크린샷으로 따로 확인했다.
- 5xx 비율은 분자에 `or vector(0)`을 붙인다. 재시작 뒤 5xx가 한 건도 없으면 시계열이 없어서 비율 전체가 빈 값이 된다.

## 열린 질문

## 변경 이력

- 2026-11-15 데이터 소스를 infra_local_set values 대신 이 저장소의 스크립트로 만들기로 했다(설계 메모).
