# 코드 버그 자동 수정

shopapi 코드 버그로 500이 나면 자동화가 원인 분석과 수정 패치를 만들고, 영향 범위로 고른 테스트를 복사본에서 돌려 통과한 수정안만 보고한다. 숨은 테스트로 그 수정이 실제로 맞는지 판정한다.

## Sub-features

- `fix-classify`: 경량 모델이 code_bug로 분류한다.
- `fix-generate`: 고성능 모델이 원인 분석과 unified diff 패치를 만든다.
- `fix-select-tests`: 바뀐 모듈의 import 관계로 테스트 파일을 고른다.
- `fix-retry`: 테스트가 실패하면 실패 로그를 붙여 다시 만든다(최대 3회).
- `fix-judge`: 숨은 테스트와 전체 테스트로 판정하고, 선별 테스트만 통과한 수정은 거짓 통과로 센다.

## How to get to it (user POV)

- 시나리오 S01~S09(`scenarios/S0*.yaml`)
- 수동: `scripts/inject.sh S01`로 사용 중인 스택에 주입하는 방법도 있지만, 검증에서는 쓰지 않는다(검증 스택 복사본만 쓴다).

## Driving it with verify-aiops

Preconditions:

- `VERIFY_REAL_LLM=1`로 launch했다. 계정에 크레딧이 있다. 한 건에 약 0.08달러다.
- `doctor.sh`가 경량 모델을 `anthropic:claude-haiku-5-5`로 보고한다.

- **수정.** `RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/drive.sh S01`. S01 행이 `outcome=resolved`, `hidden_passed=True`, `full_passed=True`, `false_pass=False`.
- **선별.** 같은 행의 `tests_selected`가 `test_files_total`보다 작다(S01은 7개 중 1개).
- **패치 확인.** `incident_S01_<id>.json`의 `patch`가 `shopapi/orders.py`를 고친다.
- **원복 확인.** `doctor.sh`의 마지막 점검(복사본이 커밋된 버전과 같음)이 OK.
- **증명.** runs.csv 행, incident JSON, worker.log.

## Gotchas

- 가짜 LLM은 sample_repo(`shop/pricing.py`)용 패치를 내서 shopapi에 적용되지 않는다. 결과는 escalated다. 가짜 LLM으로 이 기능을 증명하지 않는다.
- 패치 주입은 복사본 경로(`SHOPAPI_DIR`)에 한다. 사용 중인 `targets/shopapi`를 고치면 실험 결과가 오염된다.
- 판정은 출제자가 만든 숨은 테스트 기준이다(decisions/0004).
