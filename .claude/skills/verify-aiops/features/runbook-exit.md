# 비코드 장애 런북 안내

외부 결제 지연이나 환경변수 누락처럼 코드를 고칠 일이 아닌 장애는, 경량 모델 분류 한 번으로 끝나고 관련 런북을 안내한다. 고성능 모델은 부르지 않는다.

## Sub-features

- `runbook-alert`: 스택트레이스 없는 알림 문구(S10)를 분류하고 no_code_fix로 끝낸다.
- `runbook-startup`: 기동 실패 예외(S11, `MissingSetting: PG_API_KEY`)를 config로 분류하고 no_code_fix로 끝낸다.
- `runbook-docs`: 보고서에 참고 문서(shopapi 런북)가 붙는다.

## How to get to it (user POV)

- 시나리오 S10(알림), S11(기동 실패)
- 직접 접수: 스택트레이스 없이 `message`만 보낸다.

## Driving it with verify-aiops

Preconditions:

- `doctor.sh`가 모두 OK. 지식 문서가 들어가 있다(`results/verify/<RUN_ID>/ingest.json`).

- **알림 장애.** `RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/drive.sh S10`. `runs.csv` S10 행이 `outcome=no_code_fix`, `llm_calls=1`.
- **기동 실패.** `RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/drive.sh S11`. S11 행이 `outcome=no_code_fix`, `category=config`.
- **참고 문서.** `incident_S10_<id>.json`의 `report`에 `*참고 문서*`와 `shopapi/` 문서 이름이 있다.
- **증명.** runs.csv 두 행과 두 incident JSON.

## Gotchas

- 가짜 LLM은 메시지에 `timeout`이 있으면 infra, 그 밖은 code_bug로 분류한다. S11(`MissingSetting`)은 가짜 LLM에서 code_bug로 분류돼 수정 단계로 간다. S11 분류를 검증하려면 실제 LLM이 필요하다.
- 실제 LLM은 S10을 `dependency`로 분류하기도 한다(조건 A 본 실험). 결과(no_code_fix)는 맞다. 기대값 `infra`는 바꾸지 않는다(decisions/0011).
- 조기 종료를 끈 서버(`OPT_EARLY_EXIT=false`)에서는 원인 분석까지 호출된다. doctor로 최적화가 모두 켜졌는지 먼저 본다.
