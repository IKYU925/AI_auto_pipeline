# 실험 조건 확인

실험하는 사람이 기록하려는 조건(A 최적화 모두 켬, B 모두 끔)과 서버의 실제 설정이 같은지 확인하고, 다르면 실험을 시작하지 않는다.

## Sub-features

- `config-read`: `GET /api/v1/config`가 모델, 최적화 다섯 가지, 최대 시도, 장애당 예산을 돌려준다.
- `config-guard`: `run_experiment --condition`이 서버 설정과 다르면 실행 전에 멈춘다.

## How to get to it (user POV)

- `curl localhost:18000/api/v1/config -H "X-Automation-Token: $AUTOMATION_TOKEN"`
- `python -m scripts.run_experiment <시나리오> --condition A|B ...`

## Driving it with verify-aiops

Preconditions:

- `doctor.sh`가 모두 OK. 검증 스택은 최적화를 모두 켠 상태(A)로 뜬다.

- **조회.** `curl -s localhost:18000/api/v1/config -H "X-Automation-Token: $AUTOMATION_TOKEN"`. `optimizations`의 다섯 값이 모두 `true`.
- **불일치 중단.** `SHOPAPI_DIR=$TMPDIR/aiops-verify/$RUN_ID/targets/shopapi .venv/bin/python -m scripts.run_experiment S10 --condition B --api http://localhost:18000 --redis redis://localhost:16379/0`. 종료 코드가 0이 아니고 stderr에 `서버 설정이 조건 B와 다르다`.
- **증명.** 두 명령의 출력과 종료 코드를 `results/verify/<RUN_ID>/config.txt`에 남긴다.

## Gotchas

- `.env`의 `OPT_*`를 바꾼 뒤에는 api, worker를 다시 만들어야 서버 설정이 바뀐다. 검증 스택은 verify.compose.yml이 `OPT_*`를 모두 true로 고정한다.
