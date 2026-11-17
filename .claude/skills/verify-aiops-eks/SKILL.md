---
name: verify-aiops-eks
description: AI 장애 대응 자동화(AI_auto_pipeline)를 AWS EKS(4단계)에서 실제 감지 경로로 검증할 때 쓴다. infra_template으로 EKS 클러스터를 만들고 ArgoCD가 Git에서 앱을 모두 올리면, shopapi에 장애를 실제로 넣어(ArgoCD 버그 이미지 배포, 설정 오류, 없는 이미지 태그) 감지, 분석, 고객사별 Slack 회신, 승인 후 ArgoCD 롤백까지 run_cluster로 재고 DB, Slack, ArgoCD 이력 증거를 남긴 뒤 클러스터를 지운다. 4단계 기능(ArgoCD 배포, 배포 이력과 Pod 이벤트 컨텍스트, argocd-rollback, 고객사 분리)이나 deploy/argocd, overlays/eks를 바꾼 뒤 쓴다. AWS 과금, 실제 LLM, 실제 Slack을 쓴다. 로컬 K3s는 verify-aiops-cluster, docker compose는 verify-aiops를 쓴다.
---

# verify-aiops-eks

4단계의 사용자 경로는 3단계와 같다. 장애가 나면 Prometheus나 Loki 규칙이 알림을 띄우고, Alertmanager가 자동화 웹훅으로 보내고, 워커가 처리해 Slack 채널에 카드를 올린다. 다른 점은 배포 경로다. 앱은 ArgoCD가 이 저장소의 `origin/main`에서 배포하고, 장애 이미지와 롤백도 ArgoCD Application을 거친다. 실행기는 레포의 `scripts/run_cluster.py`(`CLUSTER=eks`)이고, 이 스킬은 클러스터 수명, 접속, 증거, 정리를 맡는다.

모든 명령은 저장소 루트(`AI_auto_pipeline/`)에서 실행한다. 필요한 것은 다음과 같다.

- 형제 저장소 `../infra_template`와 그 base 층(ECR, S3, Secrets Manager. `make eks-base-up`, `make eks-secrets AIOPS_ENV=../AI_auto_pipeline/.env`로 한 번 만든다).
- AWS 프로필 `ikgyu_admin`(`AWS_PROFILE`로 바꿀 수 있다). 루트 자격 증명은 쓰지 않는다.
- `terraform`, `aws`, `kubectl`, `gh`(로그인), `.venv`, `.env`의 `ANTHROPIC_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`.
- ECR에 `origin/main`의 `deploy/argocd/values.yaml` `imageTag`가 있을 것. 코드를 바꿨으면 `scripts/build_push.sh`로 올리고 그 태그를 main에 병합한 뒤 launch한다.

## 격리와 비용

- **클러스터는 하나이고 실행마다 만들고 지운다.** 이름(`ai-incident`)이 고정이라 두 벌을 띄울 수 없다. launch는 클러스터가 이미 있으면 멈춘다(다른 세션의 것일 수 있다). 이 실행이 만든 클러스터만 cleanup이 지운다.
- **AWS 과금이 있다.** 클러스터가 켜진 동안 시간당 약 0.34달러(추정, infra_template `docs/aiops-eks-cost.md`)다. 한 번의 검증(올리기 약 18분, 시나리오 2~3개, 내리기 약 13분)은 약 1시간이다.
- **정리 워크플로가 시간을 정한다.** 평일 09:30~12:00 밖에서는 infra_template의 워크플로가 매시 05분에 클러스터를 지운다. 저장소 변수 `EKS_HOLD_UNTIL`(`2026-11-18T13:00` 형식, 당일 13:00까지)로 늦출 수 있다. launch는 `VERIFY_MINUTES`(기본 60) 뒤에도 보호 시간 안일 때만 시작한다.
- **실험 중에는 main에 병합하지 않는다.** 루트 앱이 main을 보므로 문서만 바뀐 커밋에도 다시 sync해 넣은 장애가 풀린다. doctor가 launch 뒤 main이 바뀌었으면 FAIL을 낸다.
- **실제 LLM과 실제 Slack을 쓴다.** 코드 버그 한 건 약 $0.08~0.10, 설정 알림 한 건 $0.0003 안팎이다. `drive.sh`는 실행마다 누적 상한 `VERIFY_MAX_USD`(기본 0.5달러)를 건다. 회신은 실제 채널(`#aiops-report`, `#tenant-a-ops`, `#tenant-b-ops`)에 올라간다.

## Launch

```bash
.claude/skills/verify-aiops-eks/bin/launch.sh
export RUN_ID=<마지막 줄의 값>
```

`launch.sh`가 하는 일은 다음과 같다.

1. AWS 자격 증명, `.env` 값, 작업 트리에 주입된 버그가 없는지, 보호 시간, 클러스터가 없는지, ECR에 커밋된 이미지가 있는지 확인한다.
2. `make -C ../infra_template eks-up`(약 12~14분). 로그는 증거 폴더의 `eks_up.log`.
3. 실행 전용 kubeconfig를 만들고(`results/verify/eks-<RUN_ID>/.state/kubeconfig`), 지금 main 커밋을 적는다.
4. ArgoCD 앱 10개가 Synced/Healthy가 될 때까지 기다린다(약 4~6분, 15분 넘으면 실패).
5. `scripts/k8s_grafana.sh`, 지식 문서 수집, port-forward 5개(Alertmanager 19093, Prometheus 19090, shopapi 18001~18003)를 띄운다. port-forward는 끊기면 다시 붙는 루프이고 PID를 `.state/pf.pids`에 남긴다.
6. doctor를 돌린다. 출력 끝의 `ready`와 `RUN_ID=...`가 준비 완료 신호다.

launch가 중간에 실패해도 클러스터는 남을 수 있다. 메시지의 `RUN_ID`로 바로 cleanup한다.

## Doctor

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/doctor.sh
.claude/skills/verify-aiops-eks/bin/doctor.sh      # RUN_ID 없이: 자격 증명, 보호 시간, 클러스터 유무만
```

읽기만 한다. AWS 자격 증명, 보호 시간(지금과 20분 뒤), 클러스터 ACTIVE, 노드 Ready, ArgoCD 앱 10개 Synced/Healthy, 루트 앱 selfHeal 꺼짐, Deployment 6개 이미지가 main의 `imageTag`, shopapi의 ArgoCD 추적 표시, 권한(ai-api의 고객사 이벤트 읽기, approvals의 shopapi 앱 patch 가능과 `aiops` 앱 patch 불가), Secret 4개, api `/healthz`, 지식 문서 수, Alertmanager 웹훅 수신처, DB 시계, port-forward 5개, launch 뒤 main 불변, 주입된 버그 없음, 다른 `run_cluster` 없음을 본다. FAIL이 하나라도 있으면 drive하지 않는다.

## Drive

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S14
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S21
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S01 --tenants
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S20     # 사람이 Slack 승인 버튼을 눌러야 한다
```

인자는 `scripts.run_cluster`에 그대로 넘기고, `drive.sh`가 `CLUSTER=eks`, ECR 주소, port-forward 주소, `--max-usd`, `--out`, `--tag`를 더한다. 기능별 절차와 증명 기준은 [features/README.md](features/README.md)에 있다. `run_cluster`는 시나리오마다 이전 알림 해소를 기다리고, 장애를 넣고, 구간별 시각을 DB와 Prometheus에서 읽고, 끝나면 ArgoCD Application을 Git 기준으로 되돌린다. 한 시나리오에 3~6분 걸린다.

## Evidence

증거는 `results/verify/eks-<RUN_ID>/`에 남고 git에서 제외된다. cleanup 뒤에도 남는다(`.state/`만 지운다).

- `runs.csv`, `runs.jsonl`: 시나리오별 측정(감지, 전달, 처리, 전체 초, 결과와 분류 정답 여부, 비용, note)
- `incidents_*.json`: 장애 상태, 제안된 조치(`proposed_action`), 보고서 앞부분
- `llm_calls_*.json`, `actions_*.json`: LLM 호출, 승인 조치(`status`, `approver`, `log`)
- `argo_<앱>_*.txt`: 고객사 shopapi Application의 배포 이력(id, 시각, 이미지)
- `slack_*.json`, `worker_*.log`, `run_cluster.log`, `doctor_*.txt`, `eks_up.log`, `eks_down.log`, `timings.txt`, `ingest.json`, `drives.txt`

증명 기준은 다음과 같다.

- 실제 경로만 쓴다. 장애는 ArgoCD 배포, 설정 변경, shopapi 요청으로 만든다. 웹훅을 직접 부르거나 DB를 고치지 않는다.
- 행동과 결과를 함께 남긴다. `runs.csv` 행과 `incidents_*.json`의 최종 상태가 둘 다 있어야 한다.
- 부수 효과를 확인한다. 회신은 `slack_*.json`의 채널별 메시지 수, 배포와 롤백은 `argo_*.txt`의 이력, 승인 조치는 `actions_*.json`으로 본다.
- 숫자는 원인을 확인하고 쓴다. 전달 30초는 group_wait다. doctor의 DB 시계 항목이 OK일 때만 시간을 보고한다. 1회 측정은 1회라고 적는다.

## Cleanup

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/cleanup.sh
```

`run_cluster`가 돌고 있으면 멈춘다. 이 실행이 띄운 port-forward만 끈다(프로세스 이름으로 죽이지 않는다). 이 실행이 만든 클러스터면 `make eks-down`(약 13분)으로 루트 앱 삭제, `terraform destroy`, 남은 리소스 검사까지 하고 `down took`, `leftovers: 0`을 출력한다. `VERIFY_KEEP_CLUSTER=1`이면 클러스터를 남기고 대신 Application을 Git 기준으로 되돌린다. `.state/`(kubeconfig, PID)만 지우고 증거는 남긴다. 태그 검색(`resourcegroupstaggingapi`)에는 지운 NAT, 볼륨, 서브넷이 한동안 남아 보인다. 남은 리소스 판단은 `leftovers`로 한다.

## Helpers

모두 `bin/` 아래에 있고 실행 권한이 있다. `RUN_ID`로 같은 실행을 가리킨다.

- `launch.sh`, `doctor.sh`, `drive.sh`, `cleanup.sh`: 위 절차
- `_env.sh`: 포트, 앱 이름, `committed_tag`(origin/main의 imageTag), `kept_for <분>`(정리 워크플로 보호 시간), `export_run_cluster_env`, `psql_json`
- Slack 증거는 `../verify-aiops-cluster/bin/slack_evidence.py`를 같이 쓴다
