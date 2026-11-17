# 4단계 P1b, 4-1, 4-2. EKS에서 S14, S01 감지부터 Slack 회신까지

로컬 K3s에서 확인한 자동화를 같은 매니페스트로 EKS 클러스터 하나에 올리고, 같은 시나리오가 같은 결과를 내는지 쟀다. 2026-11-17 10:13~(KST) 한 번의 클러스터 세션에서 클러스터 생성(P1b), GitOps 배포(4-1), 실제 감지 경로 측정(4-2), 삭제까지 했다. 원본 값은 [phase4_eks.csv](phase4_eks.csv)다.

## 클러스터 생성과 배포

| 항목 | 값 | 근거 |
| --- | --- | --- |
| `make eks-up`(Terraform apply, 노드 2대 Ready) | 804초, 리소스 65개 | `eks.sh: up took 804s` |
| 노드 그룹 생성 | app 1분 19초, monitoring 1분 48초 | Terraform 로그. 11-17 새벽 첫 시도는 32분 51초였다(트러블슈팅 #26) |
| 루트 앱 `imageTag` 변경부터 앱 7개 Synced/Healthy까지 | 219초 | `kubectl -n argocd get applications` 10초 간격 확인 |
| 이미지 | ECR `aiops:base-9b15467`(arm64), Deployment 6개 모두 같은 태그 | `kubectl get deploy -A` |
| Pod | 39개 모두 Running | `kubectl get pods -A` |

루트 앱의 기본 `imageTag`(`deploy/argocd/values.yaml`의 `base-1ec6b58`)는 ECR에 없어서, 세션 중에 루트 앱 `valuesObject.imageTag`를 `base-9b15467`로 바꿨다. Terraform `image_tag` 변수로 넘기면 이 단계가 없어진다.

## S14, S01 결과

| 시나리오 | 환경 | 감지 | 전달 | 처리 | 발생부터 회신까지 | 결과(기대) | 분류 | 비용 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S14 설정값 형식 오류 | EKS | 151.0초 | 30.2초 | 5.6초 | 186.9초 | no_code_fix(no_code_fix) | config, 런북 적중 | $0.0003 |
| S14 | 로컬 K3s(11-16) | 131.3초 | 30.2초 | 4.5초 | 166.7초 | no_code_fix | config, 런북 적중 | $0.0003 |
| S01 빈 주문 0으로 나누기 | EKS | 49.7초 | 30.1초 | 30.7초 | 110.7초 | resolved(resolved), 1회 시도 | code_bug | $0.081 |
| S01 | 로컬 K3s(11-15, 두 번) | 30.5~57.4초 | 30.0초 | 30.0~30.2초 | 91.0~117.7초 | resolved | code_bug | $0.091~0.092 |

- 두 시나리오 모두 장애 1건만 접수됐고(`incidents_created` 1), 분류와 결과가 정답이었다. S01은 첫 수정안이 샌드박스 선별 테스트를 통과해 `resolved`로 끝났다.
- 전달 30초는 두 환경이 같다. Alertmanager의 `group_wait`가 정한다.
- 처리 시간도 같은 범위다. 같은 이미지, 같은 모델이라 차이가 날 이유가 없다.
- 감지 시간은 1회 측정이라 환경 차이로 말하지 않는다. S14는 CrashLoop 재시작 간격과 `for`, S01은 Loki ruler 평가 주기가 정하는데, 둘 다 장애를 넣은 시각에 따라 수십 초씩 달라진다. 로컬 S14도 131~200초로 흩어져 있다.
- 비교 대상은 같은 코드 경로를 쓴 가장 최근 로컬 실행이다. S14의 3단계 첫 측정(234~264초)은 전달이 60초였던 시기의 값이라 뺐다.

## 바꾼 것

`scripts/run_cluster.py`에 EKS 모드(`CLUSTER=eks`)를 더했다.

- 로컬은 버그 이미지를 overlay에 적고 `kubectl apply -k`로 배포한다. EKS는 ArgoCD가 배포하므로 `kubectl set image`로 넣고, 복원은 ArgoCD에 Deployment 5개의 Replace sync를 요청한다. `kubectl apply`로 덮으면 ArgoCD 추적 표시가 지워져 `eks-down`의 연쇄 삭제에서 빠진다.
- EKS에는 인그레스가 없어 shopapi를 `kubectl port-forward`로 부른다. port-forward는 Pod 하나에 붙어서, 버그 이미지로 Pod가 바뀌면 연결이 끊긴다. 첫 S01은 이것 때문에 장애를 넣기 전에 멈췄다(장애 0건, 비용 0). 연결 끊김도 다시 보내게 고친 뒤 재실행에서 `RemoteProtocolError` 한 번 뒤 `500`을 받아 진행했다.

실행 방법은 다음과 같다.

```bash
CLUSTER=eks KUBECONFIG=<eks kubeconfig> AWS_PROFILE=<운영자> \
ECR_REPOSITORY_URL=<ecr_repository_url> \
ALERTMANAGER_URL=http://127.0.0.1:19093 PROMETHEUS_URL=http://127.0.0.1:19090 \
SHOP_URLS='{"shop":"http://127.0.0.1:18001","tenant-a":"http://127.0.0.1:18002","tenant-b":"http://127.0.0.1:18003"}' \
.venv/bin/python -m scripts.run_cluster S14 S01 --max-usd 0.5 --out <폴더>/runs.csv
```

Alertmanager, Prometheus, 고객사별 shopapi는 미리 port-forward해 둔다. 지식 문서 수집(`kubectl -n aiops exec deploy/api -c api -- python -m scripts.ingest_knowledge knowledge`)과 `scripts/k8s_grafana.sh`도 클러스터마다 한 번 돌린다.

## 같이 본 것

- **승인 카드의 롤백은 실행되지 않았다.** 두 장애 모두 직전 배포(S14는 설정 변경, S01은 버그 이미지)를 원인 후보로 보고 `rollout-undo`를 제안했다. 10:43에 두 카드가 승인됐지만, `run_cluster`의 복원으로 이미 리비전이 올라가 있어(S14 제안 3, 승인 시 7. S01 제안 7, 승인 시 8) 둘 다 `failed`로 기록되고 롤백하지 않았다. 리비전 확인 안전장치가 EKS에서도 동작했다. 다만 EKS의 롤백은 ArgoCD 롤백이어야 하고 `rollout-undo`는 ArgoCD 바깥에서 Deployment를 바꾼다. 4-4에서 바꾼다.
- **회신은 공통 채널에만 갔다.** `shop` namespace는 고객사가 아니라 공통 채널(`#aiops-report`)로 간다. 고객사 채널 메시지는 0개다. 공통 채널에는 S14 카드, S01 카드, S01 패치 메시지 3개가 올라갔다(장애마다 카드 1개, 코드 버그는 패치 1개 더).

## 정리

`make eks-down`은 811초 걸렸다(루트 앱 삭제, PVC 정리, Terraform 리소스 65개 destroy). 끝의 남은 리소스 검사는 `leftovers: 0`이고, 따로 조회해도 EKS 클러스터, NAT, `Project=ai-incident` 태그의 EBS 볼륨은 0개였다. 클러스터는 10:14부터 10:58(KST)까지 약 45분 있었다. 실제 요금은 Cost Explorer 반영 뒤에 infra_template `docs/aiops-eks-cost.md` 실측 기록에 적는다. LLM 비용은 $0.0815(S14 $0.0003, S01 $0.0812)다.

증거(DB 행, 워커와 api 로그, Slack 메시지, ArgoCD 앱 상태)는 실행한 맥의 `results/verify/eks-20261117-*`에 있고 git에는 넣지 않는다.
