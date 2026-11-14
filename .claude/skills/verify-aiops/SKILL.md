---
name: verify-aiops
description: AI 장애 대응 자동화(AI_auto_pipeline)를 실제로 띄우고 사용자 경로로 검증할 때 쓴다. 대상 서비스 shopapi에 버그를 주입해 장애를 재현하고, 자동화 API(/api/v1/incidents)로 접수해 워커가 분류, 분석, 수정, 검증, 보고까지 하는지 확인하고 증거를 남긴다. 사용 중인 docker compose 스택과 분리된 aiops-verify 스택(포트 18000, 18100, 16379, 15432)을 띄우므로 실험 중에도 쓸 수 있다. 기본은 가짜 LLM(비용 없음)이고, 실제 LLM은 VERIFY_REAL_LLM=1일 때만 쓴다.
---

# verify-aiops

AI_auto_pipeline의 주 사용 지점은 HTTP API다. 장애는 shopapi(FastAPI)에서 나고, 자동화 api가 접수하고, worker가 처리한다. 이 스킬은 그 경로를 그대로 따라 검증한다. 화면은 없다.

모든 명령은 저장소 루트(`AI_auto_pipeline/`)에서 실행한다. Docker Desktop이 켜져 있어야 하고, `.env`에 `AUTOMATION_TOKEN`이 있어야 한다. 실제 LLM을 쓸 때만 `ANTHROPIC_API_KEY`와 크레딧이 필요하다.

## 비용 규칙

실제 Anthropic API 호출은 사용자에게 비용이 든다. 기본은 가짜 LLM(`fake:light`, `fake:heavy`)이다. 가짜 LLM은 sample_repo용 답을 돌려주므로 shopapi 코드 버그는 고치지 못한다(에스컬레이션으로 끝남). 코드 수정 루프를 검증해야 할 때만 `VERIFY_REAL_LLM=1`로 띄우고, `drive.sh`는 실행마다 누적 0.5달러 상한(`VERIFY_MAX_USD`)을 건다. S01 한 건은 약 0.08달러다(results/day1.md).

## Slack과의 관계

검증 스택은 Slack에 아무것도 올리지 않는다. shopapi의 Incoming Webhook과 워커의 봇 토큰을 비운다. Slack 앱의 Request URL은 사용 중인 스택의 n8n 하나만 가리키므로, Slack 경로(알림, n8n, 스레드 회신)는 이 스킬의 격리 스택으로 검증할 수 없다. 그 경로는 [features/slack-thread-reply.md](features/slack-thread-reply.md)대로, 운영자가 허락할 때만 사용 중인 스택에서 `scripts/slack_e2e.py`로 확인한다.

## Launch

```bash
.claude/skills/verify-aiops/bin/launch.sh                  # 가짜 LLM
VERIFY_REAL_LLM=1 .claude/skills/verify-aiops/bin/launch.sh  # 실제 LLM
export RUN_ID=<마지막 줄의 값>
```

`launch.sh`가 하는 일은 다음과 같다.

1. `aiops-verify` 스택이 이미 떠 있으면 멈춘다. 다른 검증이 쓰는 인스턴스를 같이 조작하지 않는다.
2. 커밋된 `targets/shopapi`를 `$TMPDIR/aiops-verify/<RUN_ID>/`에 꺼낸다. 사용 중인 `targets/shopapi`에는 실험 중 주입된 버그가 있을 수 있어서 쓰지 않는다.
3. `docker-compose.yml`에 `verify.compose.yml`을 덮어써서 redis, postgres, api, worker 2개, shopapi를 띄운다. 데이터 볼륨은 `aiops-verify_*`라 처음에는 비어 있고, `migrate` 서비스가 빈 PostgreSQL에 스키마를 만든 뒤 api와 worker가 뜬다.
4. api(18000)와 shopapi(18100)가 응답할 때까지 기다리고, 지식 문서를 PostgreSQL(`documents`)에 넣는다. 지식 문서까지 들어가야 쓸 수 있는 상태다. 출력 끝의 `ready`와 `RUN_ID=...`가 준비 완료 신호다.

## Doctor

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/doctor.sh
```

읽기만 한다. 컨테이너 6개, 스키마 최신(migrate 종료 코드 0, alembic 버전이 `migrations/versions`의 마지막 번호), 포트 소유(18000이 aiops-verify), api와 shopapi 응답, 토큰 인증과 최적화 설정(모두 켬), 경량 모델(가짜 또는 실제), shopapi 복사본이 커밋된 버전과 같은지(남은 버그 주입 없음)를 확인한다. 하나라도 FAIL이면 검증하지 말고 cleanup 후 다시 launch한다. 이상해 보이면 언제든 먼저 실행한다.

## Drive

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/drive.sh S10 S11 S13
```

시나리오 ID를 받아 `scripts/run_experiment.py`를 검증 스택에 대고 실행한다. 시나리오마다 캐시를 비우고, 버그 패치를 복사본에 주입하고(코드 버그만), shopapi로 장애를 재현해 `POST /api/v1/incidents`로 접수한다. 그다음 워커가 끝낼 때까지 `GET /api/v1/incidents/<id>`를 폴링하고, AI 패치를 숨은 테스트와 전체 테스트로 판정한 뒤 원복한다. 시나리오 목록과 재현 방식은 `scenarios/S*.yaml`, 기능별 절차는 [features/README.md](features/README.md)에 있다.

직접 한 건만 접수해 볼 때는 다음과 같다.

```bash
set -a; . ./.env; set +a
curl -s -X POST localhost:18000/api/v1/incidents -H "X-Automation-Token: $AUTOMATION_TOKEN" \
  -H 'Content-Type: application/json' -d '{"channel":"C-VERIFY","service":"shopapi","message":"PG approve timeout"}'
curl -s localhost:18000/api/v1/incidents/<incident_id> -H "X-Automation-Token: $AUTOMATION_TOKEN"
```

## Evidence

증거는 `results/verify/<RUN_ID>/`에 남고 git에서 제외된다. cleanup 후에도 지우지 않는다.

- `runs.csv`, `runs.jsonl`: 시나리오별 결과(30개 열, 절차 전체 비용 `procedure_usd` 포함, 분류, 결과, 시도, 시간, 비용, 테스트 선별, 숨은 테스트 판정, 중복 접수 수)
- `incident_<시나리오>_<id>.json`: 상태 조회 API의 응답 그대로(분류, 패치, 보고서, 비용)
- `worker.log`: 워커 로그
- `ingest.json`: 지식 문서 수집 결과

증명 기준은 다음과 같다.

- 실제 사용자 경로만 쓴다. shopapi 요청으로 장애를 만들고 공개 API로 접수한다. 상태 저장소를 직접 고치거나 워커 함수를 직접 부르지 않는다.
- 행동과 결과를 함께 남긴다. 접수 응답(incident_id)과 최종 상태 JSON이 둘 다 있어야 한다.
- 부수 효과를 확인한다. 중복 접수는 만들어진 장애 수(`incidents_created`), 재발은 LLM 호출 수(`llm_calls`)와 재사용 여부(`reused_fix`), 수정은 숨은 테스트 판정(`hidden_passed`)으로 본다.
- 가짜 LLM은 외부 경계(Anthropic API)만 대체한다. 큐, 그래프, 테스트 실행, 판정은 실제다. 가짜 LLM으로 낸 결과로 LLM 품질을 말하지 않는다.

## Cleanup

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/cleanup.sh
```

이 실행이 띄운 `aiops-verify` 프로젝트의 컨테이너와 볼륨, `$TMPDIR/aiops-verify/<RUN_ID>`만 지운다. 사용 중인 `ai_auto_pipeline` 스택과 증거는 건드리지 않는다. 프로세스 이름으로 죽이지 않는다.

## Helpers

`bin/` 아래 네 개가 전부다. 모두 실행 권한이 있고 `RUN_ID`로 같은 실행을 가리킨다. `bin/_env.sh`는 공통 값(프로젝트 이름, 경로, compose 함수)이다.
