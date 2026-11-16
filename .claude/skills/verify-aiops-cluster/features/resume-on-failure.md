# 장애 중 재개

처리 중인 워커가 죽거나 노드가 꺼져도 장애는 사라지지 않는다. 다른 워커가 Redis Streams에서 메시지를 회수해 LangGraph 체크포인트(PostgreSQL)의 중단된 단계부터 이어 가고, 끝난 단계의 LLM 호출은 다시 하지 않는다. 회신은 한 번만 간다.

## Sub-features

- 워커 Pod 강제 삭제 뒤 재개(`--kill-worker-after <단계>`)
- 노드 정지 중 재개(S16, k3s-w1 전원 차단 3분, 노드 복구 뒤 웹훅 재전송)
- 중복 회신 방지(처리 중 메시지 heartbeat, `report_ts` 확인)

## How to get to it (user POV)

장애 처리 도중 워커 Pod가 재시작되거나 노드가 내려가도 운영자는 Slack에서 카드 하나와 패치 하나만 받는다. 조금 늦게 올 뿐이다.

## Driving it with verify-aiops-cluster

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S02 --kill-worker-after root_cause
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S16 --halt-after classify
```

증명은 다음이 모두 맞을 때다.

- `runs.csv`의 `note`에 재개한 단계와 단계별 호출 수. 끝난 단계는 x1이어야 한다(예: `classifyx1,root_causex1,generate_fixx1`)
- `slack_*.json`에서 그 장애의 메시지가 2개(카드, 패치). 4개면 중복 회신이다
- `worker_*.log`에 다른 워커가 메시지를 회수한 기록

비용은 각각 약 $0.08~0.10이다.

## Gotchas

- S16은 공유 클러스터의 k3s-w1을 `vagrant halt --force`로 끈다. 자동화 api, 워커, shopapi가 모두 그 노드에 있다. 잠금을 잡은 실행에서만 돌리고, 다른 세션이 클러스터를 쓰는 중이면 건너뜀으로 보고한다.
- 노드가 꺼진 동안 웹훅을 받을 곳이 없어서 전달 시간이 180초 이상으로 나온다. 정상이다.
- 처리 시간이 회수 기준(30초)에 가까우면 heartbeat가 없을 때 중복 처리가 났다(3-8에서 수정). 회신 수를 세지 않고 "재개됐다"만 보고하면 이 회귀를 놓친다.
