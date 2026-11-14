# 재발 시 수정안 재사용

검증까지 끝난 수정안이 있는 장애가 배포 전에 다시 들어오면, LLM을 부르지 않고 저장된 수정안을 테스트만 다시 돌려 보고한다.

## Sub-features

- `reuse-hit`: 같은 지문의 검증된 수정안을 찾는다.
- `reuse-no-llm`: LLM 호출 0회.
- `reuse-retest`: 선별 테스트를 다시 돌린다.

## How to get to it (user POV)

- 시나리오 S12(base S01을 처리한 뒤 같은 장애 재접수)

## Driving it with verify-aiops

Preconditions:

- `VERIFY_REAL_LLM=1`로 launch했다(base S01을 실제로 고쳐야 재사용할 수정안이 생긴다). 약 0.08달러.

- **재발.** `RUN_ID=$RUN_ID .claude/skills/verify-aiops/bin/drive.sh S12`. S12 행이 `outcome=resolved`, `llm_calls=0`, `reused_fix=True`, `hidden_passed=True`.
- **증명.** runs.csv 행과 incident JSON의 보고서에 `[이전에 검증된 수정안 재사용]`.

## Gotchas

- 수정안 재사용을 끈 서버(`OPT_FIX_REUSE=false`)에서는 LLM이 다시 불린다.
- S12 절차는 base 실행 뒤 중복 제거 키만 지우고 캐시는 남긴다. 사이에 캐시를 비우면 재사용을 확인할 수 없다.
