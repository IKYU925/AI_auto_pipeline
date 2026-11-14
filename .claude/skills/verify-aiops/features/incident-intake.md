# 장애 접수와 중복 제거

운영자(또는 n8n, Alertmanager 어댑터)가 장애를 API로 넣으면 자동화가 바로 202로 받아 큐에 넣고, 같은 장애가 여러 번 들어와도 하나로 묶으며, 처리 상태를 조회할 수 있다.

## Sub-features

- `intake-accept`: 토큰이 맞으면 202와 incident_id, fingerprint를 돌려준다.
- `intake-auth`: 토큰이 없거나 틀리면 401.
- `intake-dedupe`: 같은 에러 지문의 장애는 중복 제거 기간 동안 같은 incident_id로 묶인다.
- `intake-status`: 상태 조회가 queued, running을 거쳐 최종 상태(resolved, escalated, no_code_fix, failed)를 보여 준다.

## How to get to it (user POV)

- `POST /api/v1/incidents`(헤더 `X-Automation-Token`)
- `GET /api/v1/incidents/<incident_id>`
- 시나리오 S13(같은 알림 10회)

## Driving it with verify-aiops

Preconditions:

- `doctor.sh`가 모두 OK.
- `set -a; . ./.env; set +a`로 `AUTOMATION_TOKEN`을 불러왔다.

- **인증 실패.** 토큰 없이 접수한다. `curl -s -o /dev/null -w '%{http_code}' -X POST localhost:18000/api/v1/incidents -H 'Content-Type: application/json' -d '{"channel":"C","service":"shopapi","message":"x"}'`. 결과는 `401`.
- **접수.** 토큰을 붙여 접수한다. `curl -s -X POST localhost:18000/api/v1/incidents -H "X-Automation-Token: $AUTOMATION_TOKEN" -H 'Content-Type: application/json' -d '{"channel":"C-VERIFY","service":"shopapi","message":"PG approve timeout order_id=1"}'`. 결과 JSON에 `incident_id`, `"status":"queued"`, `"deduplicated":false`.
- **중복.** 주문번호만 바꿔 다시 접수한다. 같은 명령에서 `order_id=2`. 결과의 `incident_id`가 앞과 같고 `"deduplicated":true`.
- **상태.** `curl -s localhost:18000/api/v1/incidents/<incident_id> -H "X-Automation-Token: $AUTOMATION_TOKEN"`. `status`가 최종 상태가 될 때까지 몇 초 간격으로 다시 조회한다. 결과를 `results/verify/<RUN_ID>/intake.txt`에 남긴다.
- **증명(S13).** `RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/drive.sh S13`. `runs.csv`의 S13 행 `incidents_created`가 `1`.

## Gotchas

- launch 직후 첫 장애는 워커가 임베딩 모델을 불러오느라 10~20초 걸린다. 한 번 조회해서 running이라고 실패로 보지 않는다.
- 중복 제거 기간(`DEDUPE_TTL_SEC`, 기본 600초)이 남아 있으면 같은 장애를 다시 접수해도 새 처리가 시작되지 않는다. drive.sh는 실행 전에 `dedupe:*`를 비운다. curl로 직접 확인할 때는 메시지를 바꾼다.
- 지문은 숫자, UUID, 시각을 지운 메시지와 스택 프레임으로 만든다. 숫자만 다른 메시지는 같은 장애다.
- S13은 가짜 LLM에서는 base(S02)를 고치지 못해 escalated로 끝난다. 검증 대상은 `incidents_created`다.
