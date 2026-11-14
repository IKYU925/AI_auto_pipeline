# F03. PostgreSQL 이전과 체크포인터 재개

상태: 구현 중(Day 8~12 완료, Day 13 다음)
관련: plan.md 8장, Day 8~13

## 문제

장애 상태, LLM 비용, 검증된 수정안이 Redis 키에 TTL을 달고 흩어져 있다. 7일(비용은 30일)이 지나면 사라지고, "서비스별 장애 수와 평균 비용" 같은 질문은 키를 전부 훑어야 답할 수 있다. 워커가 분석 도중 죽으면 그래프 상태가 메모리에만 있어서 처음부터 다시 LLM을 부른다. 3단계 대시보드(Grafana PostgreSQL 데이터 소스)와 Pod 종료 중 재개는 이 데이터가 영구 저장소에 있어야 만들 수 있다.

## 목표와 비목표

- 목표: 장애, LLM 호출, 수정안을 PostgreSQL에 영구 저장하고 SQL로 집계한다(Day 8~9).
- 목표: 지식 문서 검색을 pgvector와 pg_trgm 하이브리드(RRF)로 옮기고 에러 코드 질의 적중률을 비교한다(Day 10).
- 목표: LangGraph PostgreSQL 체크포인터로 워커 강제 종료 후 마지막 단계부터 재개한다(Day 11~12).
- 목표: 1단계와 같은 시나리오를 다시 돌려 결과가 나빠지지 않았는지 확인한다(Day 13).
- 비목표: 큐와 중복 제거 이전. Redis Streams와 짧은 TTL 키는 그대로 둔다.
- 비목표: 승인(approvals), 조치(actions) 기록 기능. 테이블만 만들고 F06에서 쓴다.

## 운영자 시나리오

1. `docker compose up -d`를 하면 `migrate` 서비스가 스키마를 최신으로 올린 뒤 api와 worker가 뜬다.
2. 장애가 접수되면 `incidents`에 한 줄이 생기고, 처리 결과와 LLM 호출이 같은 장애 ID로 쌓인다.
3. 운영자는 `psql`로 "최근 7일 서비스별 장애 수, 결과, 평균 비용"을 한 번에 조회한다.
4. 같은 장애가 배포 전에 재발하면 `fixes`의 통과한 수정안을 다시 검증만 한다.

## 요구사항

- 스키마는 plan.md 8장을 따른다. Alembic 마이그레이션 하나로 확장(`vector`, `pg_trgm`)과 테이블(`incidents`, `documents`, `fixes`, `approvals`, `actions`, `llm_calls`)을 만든다.
- `incidents`에는 plan.md 칼럼에 더해 `fingerprint`별 조회용 결과 칼럼 `result jsonb`(분류, 테스트 선별, 테스트 결과, 비용 요약, 패치 경로 등 API가 돌려주던 값)와 `report text`, `updated_at`을 둔다.
- `IncidentStore`
  - 접수 시 장애 정보(출처, 서비스, 심각도, 메시지, 스택트레이스)와 지문을 넣는다.
  - 상태 갱신 시 `attempts`, `category`, `needs_code_fix`, `report`는 칼럼에, 나머지는 `result`에 합친다. 끝 상태(resolved, escalated, no_code_fix, budget_exceeded, failed)면 `finished_at`을 남긴다.
  - `GET /api/v1/incidents/{id}` 응답 모양은 1단계와 같다. 실험 스크립트와 n8n이 그대로 동작해야 한다.
  - 중복 제거(`dedupe:*`)는 Redis에 남긴다.
- `CostTracker`: LLM 호출과 캐시 적중을 `llm_calls`에 한 줄씩 쌓는다. 요약(`usd`, `tokens_in`, `tokens_out`, `calls:<모델>`, `task:<작업>`, `cache_hit:<작업>`)과 예산 확인은 이 테이블을 집계한다.
- 수정안: 검증 시도마다 `fixes`에 한 줄을 남긴다(실패한 시도는 `passed=false`). 재사용은 같은 지문의 가장 최근 `passed=true`이고 `retired_at`이 빈 수정안을 쓴다. 위험도(`risk`)도 저장해 재사용 카드에 보인다.
- 실험 스크립트의 캐시 비움은 Redis 키를 지우고, `fixes`는 지우지 않고 `retired_at`을 채운다. 배포된 수정안을 다시 쓰지 않는 것과 같은 뜻이다.
- 테스트는 실제 PostgreSQL(pgvector 이미지)에서 돈다. 테스트마다 테이블을 비운다.

## 완료 기준

- [x] `alembic upgrade head` 후 테이블 6개와 확장 2개가 있다(`tests/test_postgres_store.py`)
- [x] `docker compose up -d`로 빈 볼륨에서 마이그레이션 후 api가 `/healthz`에 응답한다(verify-aiops 스택, `results/verify/20261114-170144`)
- [x] 기존 파이프라인 테스트가 PostgreSQL 저장소로 모두 통과한다(71개, 새 테스트 포함 76개)
- [x] 같은 장애 ID로 `incidents` 한 줄, `llm_calls` 호출 수만큼, `fixes` 시도 수만큼 쌓인다(테스트, 검증 스택 S01 시도 3건)
- [x] 서비스별 장애 수와 평균 비용을 SQL 한 번으로 조회한다(테스트, 검증 스택 `postgres_rows.txt`)
- [x] 지식 문서 수집과 검색이 `documents` 테이블을 쓰고, 앱에서 Qdrant 의존이 없다(테스트 78개, 검증 스택 S10이 shopapi 결제 지연 런북을 찾음)
- [x] 에러 코드 질의 10개의 상위 3개 적중률과 검색 지연을 Qdrant 벡터, pgvector 벡터, 하이브리드로 비교한 결과가 있다([phase2_retrieval.md](../../results/phase2_retrieval.md): 8/10, 8/10, 9/10)
- [x] 테스트 단계에서 워커가 죽으면 다른 워커가 회수해 `run_tests`부터 이어 가고, 분류, 원인 분석, 첫 수정안 생성을 다시 부르지 않는다(`tests/test_resume.py`)
- [x] 회신을 마친 메시지가 다시 처리돼도 Slack 회신은 한 번이다(`incidents.report_ts`)
- [x] 체크포인트를 끄면(`CHECKPOINT_ENABLED=false`) 회수한 메시지를 처음부터 다시 처리한다. 재개 실험의 비교 조건이다
- [x] 워커 강제 종료 실험 결과가 있다. 체크포인터 켬과 끔, 종료 시점 2곳, 각 3회([phase2_kill.md](../../results/phase2_kill.md))
- [ ] Day 13 완료 기준은 해당 작업을 시작할 때 이 문서에 추가한다

## 설계 메모

- 저장소 클래스(`IncidentStore`, `CostTracker`)의 메서드는 그대로 두고 내부만 바꾼다. 호출부(API, 워커, 라우터, 그래프)를 건드리지 않아서 이전 전후 비교가 저장소 차이만 보여 준다.
- 드라이버는 psycopg 3과 커넥션 풀이다. 체크포인터(`langgraph-checkpoint-postgres`)도 psycopg 3을 쓴다.
- LLM 응답 캐시는 Redis에 남긴다. [0012](../decisions/0012-postgres-scope.md) 참고.
- 체크포인터는 `thread_id = incident_id`다. 워커는 메시지를 받으면 체크포인트를 먼저 본다. 남은 단계가 있으면 그 단계부터 잇고, 그래프가 끝났으면 저장과 회신만 하고, 없으면 새로 시작한다.
- 체크포인터 테이블은 `migrate` 서비스(`python -m app.db`)가 Alembic 다음에 만든다. 라이브러리가 자기 마이그레이션 버전을 따로 관리하기 때문이다.
- 재개하면 보고 단계가 다시 돌 수 있다. 그래서 `fixes`에 `(incident_id, attempt, passed)` 유일 조건을 두었다(0002).
- `checkpoint_enabled`는 `OPT_*` 묶음에 넣지 않았다. 비용 최적화가 아니라 장애 복구 기능이라, 넣으면 1단계 A/B 조건 확인이 깨진다.

## 열린 질문

- 없음

## 변경 이력

- 2026-11-14 강제 종료 실험의 종료 시점을 plan.md의 "원인 분석 직후"에 더해 "첫 수정안 생성 직후"로 늘렸다. 원인 분석 응답이 1단계부터 캐시되므로, 원인 분석 직후만 재면 체크포인터의 비용 효과가 보이지 않는다(실험에서 확인).
- 2026-11-14 강제 종료 실험은 가짜 LLM(호출당 3초 지연)으로 했다. 재처리 호출 수는 그래프 경로로 정해지므로 실제 모델과 같다. 비용은 1단계 실측값으로 환산했다.

- 2026-11-14 검색 비교용 방해 문서 12개를 `eval/retrieval/distractors`에 따로 두었다. 지식 문서 7개만으로는 세 방식 모두 거의 만점이라 비교가 되지 않는다. 운영 지식 베이스에는 넣지 않는다.
- 2026-11-14 Qdrant는 compose의 `eval` 프로필로만 남겼다. 기준선을 다시 잴 수 있게 평가 스크립트 안에 1단계 방식(벡터만)의 최소 구현을 두었다.

- 2026-11-14 실험 초기화가 `fixes`를 지우면 앞 시나리오의 시도 기록이 사라졌다(검증 스택에서 S01의 시도 3건이 S10 초기화 때 삭제). 지우는 대신 `retired_at`으로 재사용 대상에서만 뺀다.
