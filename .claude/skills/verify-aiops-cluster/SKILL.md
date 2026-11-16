---
name: verify-aiops-cluster
description: AI 장애 대응 자동화(AI_auto_pipeline)를 로컬 K3s(infra_local_set)에서 실제 감지 경로로 검증할 때 쓴다. shopapi에 장애를 실제로 넣고(버그 이미지, 설정 오류, 디스크 채우기, 노드 정지) Prometheus·Loki 규칙, Alertmanager 웹훅, 자동화 분석, 고객사별 Slack 회신, 승인 후 조치까지 이어지는지 run_cluster로 재고 DB, Slack, 로그 증거를 남긴다. 3단계 기능(감지 경로, 고객사 분리, 승인 조치, 재개)을 바꾼 뒤나 클러스터를 새로 띄운 뒤 쓴다. 실제 LLM과 실제 Slack을 쓰며 클러스터는 하나라 잠금을 잡는다. docker compose 경로(1, 2단계)는 verify-aiops를 쓴다.
---

# verify-aiops-cluster

3단계부터 이 자동화의 사용자 경로는 HTTP 접수가 아니라 모니터링이다. 장애가 나면 Prometheus나 Loki 규칙이 알림을 띄우고, Alertmanager가 `ai-api.aiops.svc:8000/api/v1/alerts/alertmanager`로 보내고, 워커가 처리해 Slack 채널에 카드를 올린다. 이 스킬은 장애를 실제로 넣어 그 경로 전체를 확인한다. 실행기는 레포의 `scripts/run_cluster.py`이고, 이 스킬은 그 앞뒤(배포 상태 맞추기, 잠금, 증거, 정리)를 맡는다.

모든 명령은 저장소 루트(`AI_auto_pipeline/`)에서 실행한다. 필요한 것은 다음과 같다.

- 형제 저장소 `../infra_local_set`의 클러스터(VM 3대)가 떠 있을 것. 클러스터 자체 점검은 그 레포의 `verify-k3s-lab` 스킬이 맡는다.
- Docker Desktop의 `insecure-registries`에 `192.168.56.10:30500`(이미지를 다시 올릴 때만).
- `.env`의 `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`.
- `.venv`(`python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt`).

## 격리와 비용

- **클러스터는 하나다.** VM 이름과 IP가 고정이라 두 벌을 띄울 수 없다. `launch.sh`가 `aiops` namespace에 ConfigMap `verify-aiops-cluster-lock`을 만들어 잠근다. 잠금이 있으면 다른 실행은 시작하지 않는다. 사람이 `run_cluster`를 직접 돌리는 중이어도 doctor가 FAIL을 낸다.
- **실제 LLM과 실제 Slack을 쓴다.** 클러스터 Secret에 실제 키가 들어 있어 가짜 LLM으로 바꿀 수 없다. 비용은 S01 같은 코드 버그 한 건이 약 $0.08, 설정·인프라 알림 한 건이 $0.0003 안팎이다. `drive.sh`는 실행마다 누적 상한 `VERIFY_MAX_USD`(기본 0.5달러)를 건다. 회신은 실제 채널(공통 `#aiops-report`, `#tenant-a-ops`, `#tenant-b-ops`)에 올라간다.
- **스택을 지우지 않는다.** 자동화와 shopapi는 사람이 Grafana로 보는 상시 환경이다. 검증은 커밋된 상태로 맞춰 쓰고, 끝나면 커밋된 상태로 되돌린다.

## Launch

```bash
.claude/skills/verify-aiops-cluster/bin/launch.sh
export RUN_ID=<마지막 줄의 값>
```

`launch.sh`가 하는 일은 다음과 같다.

1. 클러스터 접속, `.env` 값, 작업 트리에 주입된 버그가 없는지 확인한다.
2. 잠금을 잡는다. 이미 있으면 멈춘다.
3. 커밋된 overlay 태그(`deploy/k8s/overlays/local/kustomization.yaml`의 `newTag`) 이미지가 레지스트리에 없으면 HEAD로 빌드해 그 태그로 올린다. 클러스터를 새로 만들면 레지스트리가 비어 있어서 필요하다. 그 태그의 커밋 이후 코드가 바뀌었으면 빌드하지 않고 멈춘다(overlay 태그를 커밋해야 한다).
4. `scripts/k8s_secrets.sh`, `kubectl apply -k deploy/k8s/overlays/local`, 모든 Deployment와 StatefulSet의 rollout 완료, `scripts/k8s_grafana.sh`, 지식 문서 수집(`scripts.ingest_knowledge`)을 한다. 빈 PostgreSQL이면 api Pod의 `migrate` init 컨테이너가 스키마를 만든다.
5. doctor를 돌린다. 출력 끝의 `ready`와 `RUN_ID=...`가 준비 완료 신호다.

이미 맞춰진 클러스터에서는 바뀌는 것이 없고 1~2분이면 끝난다. 새 클러스터에서는 이미지 빌드 때문에 5분 이상 걸린다.

## Doctor

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/doctor.sh
```

읽기만 한다. 노드 3개 Ready, 커밋된 태그 이미지가 레지스트리에 있음, 8개 워크로드가 준비됐고 Deployment 이미지가 커밋된 태그임(버그 이미지 없음), Secret 값 4개, api `/healthz`, 지식 문서 수, Alertmanager에 자동화 웹훅 수신처, DB(k3s-w1)와 Prometheus(k3s-w2) 시계가 맥과 5초 안, 작업 트리에 주입된 버그 없음, 다른 `run_cluster` 실행 없음을 본다. firing 알림 수와 잠금 주인은 INFO로 보여준다. FAIL이 하나라도 있으면 drive하지 않는다. 무언가 이상해 보이면 먼저 이것부터 돌린다.

## Drive

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S14
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S01 --tenants
```

인자는 `scripts.run_cluster`에 그대로 넘긴다. `drive.sh`가 `--max-usd`, `--out`, `--tag`를 더한다. 시나리오 정의는 `scenarios/S*.yaml`의 `cluster:` 블록이고, 기능별 절차와 옵션은 [features/README.md](features/README.md)에 있다.

`run_cluster`가 시나리오마다 하는 일은 다음과 같다. 자동화로 가는 알림이 모두 풀릴 때까지 기다린다. 장애를 넣는다. 알림 시작, 자동화 수신, 처리 완료, Slack 회신 시각을 DB와 Prometheus에서 읽는다. 끝나면 `restore()`로 커밋된 매니페스트에 되돌린다. 한 시나리오에 3~6분 걸린다(대부분 감지와 알림 대기).

## Evidence

증거는 `results/verify/cluster-<RUN_ID>/`에 남고 git에서 제외된다(`results/verify/`). cleanup 뒤에도 남는다.

- `runs.csv`, `runs.jsonl`: 시나리오별 측정(감지, 전달, 처리, 전체 초, 결과와 분류 정답 여부, LLM 호출 수, 비용, note)
- `incidents_*.json`, `llm_calls_*.json`, `actions_*.json`: drive 시작 이후 DB에 생긴 장애, LLM 호출, 승인 조치
- `slack_*.json`: 같은 시간 이후 회신 채널별 메시지(`slack_evidence.py`)
- `worker_*.log`, `run_cluster.log`: 워커 로그, 실행기 진행 로그
- `doctor_*.txt`, `ingest.json`, `drives.txt`

증명 기준은 다음과 같다.

- 실제 경로만 쓴다. 장애는 shopapi 요청, 버그 이미지 배포, 설정 변경, 디스크 채우기, 노드 정지로 만든다. 웹훅을 직접 부르거나 DB를 고치지 않는다. API로 직접 접수하는 경로는 verify-aiops의 몫이다.
- 행동과 결과를 함께 남긴다. `runs.csv` 행(행동과 시각)과 `incidents_*.json`(최종 상태)이 둘 다 있어야 한다.
- 부수 효과를 확인한다. 회신은 `slack_*.json`의 채널별 메시지 수(장애마다 카드 1개, 코드 버그는 패치 메시지 1개 더), 중복은 `incidents_created`와 메시지 수, 승인 조치는 `actions_*.json`의 `status`, `approver`로 본다.
- 숫자는 원인을 확인하고 쓴다. 감지와 전달 시간은 규칙의 `for`, 평가 주기, group_wait가 정한다. 시계가 어긋나면 모든 시간이 틀리므로 doctor의 시계 항목이 OK일 때만 시간을 보고한다.

## Cleanup

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/cleanup.sh
```

`run_cluster`가 아직 돌고 있으면 멈춘다. 프로세스를 죽이지 않는다. Deployment 이미지가 커밋된 태그가 아니거나 overlay 파일이 바뀌어 있으면 `restore()`로 되돌린다. drive가 중간에 죽은 경우다. 그다음 이 실행의 잠금만 푼다. 다른 실행의 잠금이면 아무것도 하지 않는다. 스택과 증거는 남긴다.

## Helpers

모두 `bin/` 아래에 있고 실행 권한이 있다. `RUN_ID`로 같은 실행을 가리킨다.

- `launch.sh`, `doctor.sh`, `drive.sh`, `cleanup.sh`: 위 절차
- `slack_evidence.py <증거 폴더> <since ISO8601>`: 회신 채널별 메시지를 JSON으로 출력. `drive.sh`가 부른다
- `_env.sh`: 주소, 잠금 이름, 커밋된 태그 읽기, `psql_json`
