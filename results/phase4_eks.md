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

## 2차 세션. 4-3, 4-4 S20(배포 직후 장애와 승인 후 ArgoCD 롤백)

같은 날 11:28~(KST) 두 번째 클러스터 세션에서 S20을 쟀다. 이미지는 `argocd-rollback`이 들어간 `base-715cba6`이고, shopapi는 고객사별 Application(`shopapi-shop` 등)으로 나뉘어 있다(#61).

| 항목 | 값 | 근거 |
| --- | --- | --- |
| `make eks-up` | 843초 | `eks.sh: up took 843s` |
| eks-up 뒤 앱 10개 Synced/Healthy | 241초. 손으로 바꾼 값 없음 | `values.yaml`의 `imageTag`가 ECR 태그와 맞음(#60, #62) |
| ArgoCD 추적 표시 | `shopapi-shop:apps/Deployment:shop/shopapi` | 4-4 코드가 가정한 형식과 같음. 배포 이력 ID는 0부터 |
| approvals 권한 | `shopapi-shop` patch 가능, `aiops` 앱 patch 불가 | `kubectl auth can-i` |

| 구간 | S20 EKS(3차 실행) | S20 로컬 K3s(3-10b) |
| --- | --- | --- |
| 감지 / 전달 / 처리 | 46.9초 / 30.1초 / 32.3초 | - |
| 발생부터 회신까지 | 109.5초, resolved, 1회 시도, $0.083 | - |
| 알림부터 승인 요청까지 | 62.6초 | - |
| 승인부터 조치 끝까지 | 6.4초(`argocd-rollback`, 이력 5 -> 6) | 6초(`rollout-undo`) |
| 승인부터 알림 해소까지 | 105.2초 | 119초 |

- **4-3.** 보고서가 "장애 1분 전(03:20:30Z)에 리비전 5(이미지 `s20-87485dc-d600a24`)가 배포되었다"고 직전 배포를 원인 후보로 적었다. 리비전 번호와 시각은 ArgoCD 배포 이력에서 읽은 값이다.
- **4-4.** 롤백은 Application의 이미지를 이력 4의 값(`base-715cba6`)으로 되돌렸고, 자동 sync가 새 이력 6으로 배포했다. 조치 기록은 `succeeded`, 승인자가 남았다. 세션 동안 회신은 공통 채널에만 갔다(2차, 3차 실행 합쳐 메시지 4개, 고객사 채널 0개).
- 숫자는 1회 측정이다. 승인 대기(72.1초)는 사람이 버튼을 누르기까지의 시간이라 처리 시간에 넣지 않는다.

### 앞선 두 번의 실패

- **1차. 루트 앱 selfHeal.** 버그 이미지 배포(이력 1, 02:47:29Z) 1초 뒤 기본 이미지 배포(이력 2)가 쌓였다. 루트 앱 `aiops-root`가 `selfHeal: true`라 하위 Application 객체의 이미지 변경을 Git 값으로 되돌렸다. 장애 요청이 200으로 끝나 900초 시간 초과. 실행 중인 루트 앱의 selfHeal을 끄고 다시 돌렸고, Terraform 수정은 infra_template #32다.
- **2차. 실험 중 main 병합.** 승인 요청까지 정상(알림부터 57.7초)이었지만, 실험 중에 문서 PR(#63)이 `main`에 병합됐다. 루트 앱이 새 커밋으로 자동 sync하면서 하위 앱 이미지를 Git 값으로 맞췄고(03:06:51Z), 알림은 승인 629초 전에 해소됐다. 승인된 롤백은 "제안 뒤 배포가 바뀌었다(이력 3, 지금 4)"로 실행되지 않았다. 안전장치가 의도대로 동작했다. Git이 바뀌면 Git 값이 이긴다는 GitOps 동작이므로, 실험 동안에는 `main`에 병합하지 않는다.

S01 `--tenants`(EKS 고객사 분리)와 S21은 시간이 모자라 이번 세션에서 빼고 3차 세션으로 넘겼다.

## 3차 세션. 4-3 S21(K8s 이벤트 컨텍스트), 4-5 S01 `--tenants`(고객사 분리)

같은 날 13:08~(KST) 세 번째 클러스터 세션이다. 이미지는 Pod 이벤트 컨텍스트(#65)가 들어간 `base-92322d4`다. 루트 앱은 Terraform에서 selfHeal을 끈 상태로 만들어졌다(infra_template #32). EKS S21은 없는 이미지 태그를 `shopapi-shop` Application으로 배포한다(#68). 그래서 ArgoCD 배포 이력에 남는다.

| 항목 | 값 |
| --- | --- |
| `make eks-up` | 707초 |
| eks-up 뒤 앱 10개 Synced/Healthy | 334초. 손으로 바꾼 값 없음 |
| `make eks-down` | 790초, `leftovers: 0` |
| ai-api의 고객사 namespace 이벤트 읽기 | `kubectl auth can-i list events -n tenant-a` yes |

| 시나리오 | 감지 | 전달 | 처리 | 발생부터 회신까지 | 결과(기대) | 분류 | 비용 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S21 없는 이미지 태그 | 90.9초 | 30.1초 | 5.7초 | 126.8초 | no_code_fix(no_code_fix), 런북 적중 | config | $0.0003 |
| S01 tenant-a | 42.3초 | 30.1초 | 32.6초 | 105.2초 | resolved(resolved), 1회 시도 | code_bug | $0.088 |
| S01 tenant-b | 55.2초 | 30.0초 | 32.0초 | 117.5초 | resolved(resolved), 1회 시도 | code_bug | $0.095 |

- **4-3 S21.** 분류 근거에 "존재하지 않는 ECR 이미지 태그(`aiops:s21-missing`)를 참조해 NotFound로 이미지 pull이 실패"가 나왔다. 알림 문구에는 태그도 NotFound도 없으므로, Pod 상태와 K8s 이벤트에서 읽은 값이다. 런북(`k8s/image-pull.md`)을 안내하는 짧은 경로라 원인 분석 단계는 돌지 않았다. 롤백 제안(`argocd-rollback @ shopapi-shop`, 이력 1 -> 0)은 ArgoCD 배포 이력에서 나왔다. 로컬 K3s S21(처리 13.9~17.3초)보다 처리가 짧은 것은 1회 측정이라 해석하지 않는다.
- **4-5 고객사 분리.** 같은 버그를 tenant-a, tenant-b에 차례로 넣었다. 회신은 각 고객사 채널에 2개씩(카드, 패치)이었다. 공통 채널에는 회신이 0개였다. 근거 문서는 각자의 에스컬레이션 문서와 공통 문서였고, 다른 고객사 문서는 0개였다. 롤백 제안도 각자의 Application(`shopapi-tenant-a`, `shopapi-tenant-b`)만 가리켰다. 로컬 K3s 3단계 결과와 같다.
- **승인 두 번.** 실행 중에 운영자가 tenant-a 카드를 승인했고, 3.4초 만에 `shopapi-tenant-a` 이력 1 -> 2로 롤백됐다. S21 카드도 승인했지만 실행되지 않았다. 그 사이 `run_cluster`가 복원 배포를 해서(이력 3) 리비전 확인에 걸렸다. 공통 채널의 메시지 1개는 이 승인 결과다. 복원이 끝난 뒤 들어온 승인을 막는 것이 이 안전장치의 목적이다.
- **실행되지 않은 tenant-b 승인.** tenant-b 카드(13:38:38)는 실행이 끝난 뒤 승인됐다. 그때는 `eks-down`(13:41~)이 루트 앱과 함께 승인 처리 Pod를 지운 뒤라 클릭을 받을 곳이 없었다. 그래서 조치 기록도 Slack 응답도 없다. 클러스터가 있었더라도 복원 배포 뒤라 리비전 확인에 걸렸을 것이다. 세션이 끝난 뒤의 카드 버튼은 무효다. 지금은 눌러도 아무 반응이 없어 실패인지 알 수 없다. 실험 환경에서만 생기는 일이라 코드는 고치지 않았다.
- 정리 워크플로의 연장 한도(13:00)가 지난 뒤라 14:05 정리 전에 끝내도록 시나리오를 둘로 줄였다.

## 4차 세션. 전체 시나리오와 숨은 테스트 채점(2026-11-23)

2026-11-23 09:52~12:50(KST) 네 번째 클러스터 세션이다. `verify-aiops-eks` 스킬로 실행했다(RUN_ID `20261123-095203`). 원본 값은 [phase4_eks_full.csv](phase4_eks_full.csv)다.

- 이미지는 `base-4df6d9d`다. 시나리오를 시작하기 전에 #76(Grafana 데이터 소스 GitOps), #78(승인 버튼 만료)을 병합하고 `imageTag`를 올렸다(#80). 그 뒤로는 main을 바꾸지 않았다.
- 코드 버그는 이번부터 숨은 테스트로 채점한다(#74).
- `eks-up` 708초, ArgoCD 앱 10개 Healthy까지 190초.
- #76은 실제 클러스터에서 확인했다. init 컨테이너가 데이터 소스 Secret을 처음부터 만들었고(이름으로 제한한 권한으로 생성 가능), Grafana 데이터 소스 상태가 "Database Connection OK"였다.

| 시나리오 | 유형 | 결과 | 결과 맞음 | 분류 맞음 | 감지(초) | 전달(초) | 처리(초) | 전체(초) | 비용($) | 숨은 테스트 | 거짓 통과 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S14 | 설정 | no_code_fix | O | O | 157.5 | 30.1 | 6.8 | 194.7 | 0.0004 | | |
| S15 | 인프라 | no_code_fix | O | O | 67.2 | 30.1 | 3.4 | 100.8 | 0.0005 | | |
| S10 | 인프라 | no_code_fix | O | O | 168.9 | 30.0 | 2.4 | 201.5 | 0.0003 | | |
| S11 | 설정 | no_code_fix | O | O | 156.7 | 30.1 | 2.0 | 189.0 | 0.0004 | | |
| S21 | 설정 | no_code_fix | O | O | 75.2 | 30.1 | 2.6 | 108.0 | 0.0004 | | |
| S01 | 코드 버그 | resolved | O | O | 62.5 | 30.1 | 27.6 | 120.5 | 0.075 | 통과 | 아님 |
| S02 | 코드 버그 | resolved | O | O | 49.5 | 30.1 | 25.1 | 105.0 | 0.075 | 통과 | 아님 |
| S08 | 코드 버그 | resolved(기대 escalated) | X | O | 49.1 | 30.1 | 27.7 | 107.1 | 0.082 | 실패 | **거짓 통과** |
| S12 | 재발 | resolved(수정안 재사용) | O | O | 27.7 | 30.1 | 2.5 | 60.5 | 0 | | |

- **S08은 숨은 테스트 채점이 거짓 통과로 잡았다.** 자동화 안의 선별 테스트는 통과해 `resolved`로 회신했지만, 숨은 테스트와 전체 테스트가 실패했다. 1단계, 3단계와 같은 결과다. 지금까지 3, 4단계 표의 `resolved`에는 이런 경우가 섞여 있을 수 있었다.
- 전달 30초는 Alertmanager `group_wait`다. 감지 시간은 장애를 넣은 시각과 규칙 평가 주기에 따라 달라진다.
- 시나리오마다 1회 측정이다.

### 돌리지 못한 것

- **S20(승인 후 롤백).** 첫 시도는 아래 ArgoCD 이력 문제로 장애 배포 확인에서 멈췄다. 고친 뒤 다시 돌려 승인 카드가 올라왔지만(11:37), 15분 안에 승인이 없어 시간 초과로 끝났다. 승인 서비스 로그에 클릭이 없었다.
- **S13(중복 알림), S01 `--tenants`.** port-forward가 끊겨 S13은 shopapi에 요청을 보내지 못했고, `--tenants`는 doctor에서 멈췄다. 그 뒤 doctor가 응답 없는 port-forward에 걸려 몇 분 멈춰 보호 시간 안에 다시 돌릴 수 없었다.

### 실행 중 찾은 문제

- **ArgoCD 배포 이력은 10개까지만 남는다.** 실행기는 이력 개수가 늘었는지로 배포 완료를 판단했다. 한 클러스터에서 배포를 10번 넘게 하자(S08부터) 개수가 늘지 않아 5분 기다린 뒤 실패했다. 마지막 이력 id로 판단하게 고쳤다.
- **`kubectl port-forward`는 대상 Pod가 바뀌어도 끝나지 않는다.** 죽은 Pod를 붙잡은 채 연결만 거부해서 다시 붙는 루프가 돌지 않았다. 응답이 없으면 직접 끊고 다시 붙게 고쳤다. doctor의 Alertmanager 조회에는 시간 제한이 없어 이 상태에서 멈췄다. 시간 제한을 넣었다.
- launch가 띄운 shopapi port-forward 루프 3개는 세션 초반에 이미 없었다. 원인은 확인하지 못했다.

### 정리

`make eks-down`은 1671초 걸렸다. 지금까지(762~811초)의 두 배다. NAT 게이트웨이 삭제 5분 41초, app 노드 그룹 삭제 8분 15초가 길었다. 원인은 확인하지 않았다. 남은 리소스는 `leftovers: 0`, 끝난 뒤 `aws eks list-clusters`도 비어 있었다. 클러스터는 09:52부터 13:00(KST)까지 약 3시간 켜져 있었다.

## AWS 비용

2026-11-18 09:30(KST)에 Cost Explorer로 조회했다. 값은 아직 추정치(Estimated)이고 일 단위는 UTC다. 계정에 크레딧이 있어 사용액 $2.07이 크레딧 $2.07로 상계되었고, 실제 청구는 0이다. 아래는 크레딧 전 사용액(`RECORD_TYPE=Usage`)이다.

| 항목 | 11-16 UTC | 11-17 UTC | 합계 |
| --- | --- | --- | --- |
| NAT 데이터 처리 | $0.17 (2.9GB) | $0.58 (9.8GB) | $0.75 |
| NAT 시간 | $0.12 (2시간) | $0.24 (4시간) | $0.35 |
| 노드 | $0.15 (t4g.large 0.7시간, m7i-flex.large 0.8시간) | $0.31 (t4g.large 3.7시간) | $0.46 |
| EKS 컨트롤 플레인 | $0.10 (0.95시간) | $0.22 (2.17시간) | $0.31 |
| EBS gp3 | $0.09 | $0.06 | $0.14 |
| 공인 IPv4, Secrets Manager, S3, ECR, Cost Explorer API | | | $0.05 |
| **합계** | **$0.63** | **$1.44** | **$2.07** |

- 11-16 UTC는 11-17 01:06(KST)의 첫 `eks-up` 시도(트러블슈팅 #26, 애드온 오류로 멈춘 뒤 내림)다. 11-17 UTC는 세 번의 세션이다.
- 11-17의 EKS 컨트롤 플레인이 2.17시간뿐이라, 세 세션(합계 3시간 안팎으로 추정)이 다 반영되지 않았을 수 있다. 월말 확정 뒤 다시 확인한다.
- 가장 큰 항목은 NAT 데이터 처리다. 클러스터를 새로 만들 때마다 노드가 ECR 이미지, Helm 차트, 컨테이너 이미지를 NAT로 받는다. 네 번 올리는 동안 12.7GB, 한 번에 약 3GB다. 계산 문서는 하루 3GB로 가정했는데, 하루에 여러 번 올리면 그만큼 늘어난다.
- `m7i-flex.large` 0.8시간은 첫 시도에만 있다. 설정상 노드 타입은 t4g.large라 출처를 확인하지 못했다.
- 비용 할당 태그 `Project`가 Billing에서 활성화되지 않아, 태그별로 보면 전부 태그 없음이다. 이 계정은 이 프로젝트만 쓰므로 계정 전체 값을 이 실험의 비용으로 본다.
- LLM 비용(Anthropic)은 별도이고 각 절의 비용 열에 있다.

## 정리

`make eks-down`은 811초 걸렸다(루트 앱 삭제, PVC 정리, Terraform 리소스 65개 destroy). 끝의 남은 리소스 검사는 `leftovers: 0`이고, 따로 조회해도 EKS 클러스터, NAT, `Project=ai-incident` 태그의 EBS 볼륨은 0개였다. 클러스터는 10:14부터 10:58(KST)까지 약 45분 있었다. 실제 요금은 아래 "AWS 비용" 절과 infra_template `docs/aiops-eks-cost.md` 실측 기록에 있다. LLM 비용은 $0.0815(S14 $0.0003, S01 $0.0812)다.

증거(DB 행, 워커와 api 로그, Slack 메시지, ArgoCD 앱 상태)는 실행한 맥의 `results/verify/eks-20261117-*`에 있고 git에는 넣지 않는다.
