# 4단계 P0. 로컬 K3s 자원 사용량 실측

EKS 노드 크기와 대수를 정할 근거다(plan.md 10-4 P0, infra_template `docs/plans/aiops-eks-design.md` 6장). 2026-11-16 새로 만든 로컬 K3s(Vagrant 3노드, arm64)에 3단계 스택 전체를 올리고 `kubectl top`(metrics-server)으로 쟀다.

## 측정 방법

- 표본기: `results/phase4_p0/sample_top.sh`. 노드와 Pod의 CPU(m), 메모리(Mi, working set)를 CSV로 남긴다.
- 대기: 배포 후 5분, 30초 간격 10회(`idle.csv`).
- 장애 처리: S01(코드 버그)과 S14(설정 오류)를 `run_cluster`로 실제 감지 경로에 넣고 15초 간격 44회(`load.csv`, `p0_scenarios.csv`). LLM 비용은 S01 $0.078, S14 $0.0003.
- ArgoCD: chart `argo/argo-cd` 10.17.2(v3.6.1)를 dex, notifications를 끄고 설치한 뒤 5분, 30초 간격 10회(`argocd.csv`). Application은 하나도 없다. 측정 후 지웠다.
- 노드 메모리에는 OS와 k3s 자체 사용량이 들어 있다. EKS 노드와 그대로 비교하지 않고, namespace 합계를 크기 산정에 쓴다.

## 결과

namespace별 메모리(Mi). 대기는 평균, 처리 중과 ArgoCD 설치 후는 최대다.

| namespace | 대기 | 장애 처리 중 | ArgoCD 설치 후 | 비고 |
| --- | --- | --- | --- | --- |
| monitoring | 1257 | 1322 | 1401 | Prometheus 522, Grafana 410, Loki 142 |
| aiops | 287 | 912 | 912 | 워커 1개가 임베딩 모델을 올린 상태(아래) |
| shop + tenant-a + tenant-b | 160 | 162 | 160 | shopapi 4개 |
| argocd | - | - | 157 | server 52, applicationset 31, repo-server 27, controller 26 |
| kube-system | 90 | 95 | - | k3s 기준. EKS 애드온과 다르다 |

CPU는 어디서도 병목이 아니었다. 장애 처리 중 namespace 최대는 aiops 563m(워커가 테스트를 돌리는 순간), monitoring 312m이고, 노드 최대는 k3s-w1 805m다(할당 2코어).

## 해석

- **워커 메모리는 첫 장애에서 계단처럼 오른다.** 워커는 69Mi로 떠 있다가, 장애를 처음 처리할 때 임베딩 모델(fastembed)을 올려 670Mi가 되고 내려오지 않는다(`load.csv`, 02:38:32에 622Mi, 02:38:47부터 668~672Mi 유지. S14 때 다른 워커도 같은 모양). 그래서 대기 287Mi는 크기 산정에 쓸 수 없다. 워커 2개가 모두 모델을 올린 상태를 기준으로 aiops는 약 1.5Gi로 본다(측정값 912Mi + 두 번째 워커 증가분 약 600Mi, 추정).
- **워커 requests가 실제보다 작다.** 매니페스트는 requests 256Mi, limit 768Mi다. 실측 670Mi는 limit의 87%다. 스케줄러는 256Mi로 자리를 잡으므로 노드가 빠듯하면 과다 배치될 수 있다. P2에서 requests를 실측에 맞춘다(설계 문서 리스크 표의 "모든 매니페스트에 requests/limits 명시").
- **장애 처리 중 최대 912Mi의 다른 원인.** 02:44 전후 최대값은 실험 정리 단계의 재배포로 이전 Pod와 새 Pod가 함께 떠 있던 시점이다. 롤링 업데이트 중에는 이만큼 겹친다는 점도 여유분에 넣는다.
- **ArgoCD 최소 설치는 약 150Mi다.** 관리하는 Application이 없을 때 값이다. app-of-apps로 앱 10여 개를 붙이면 controller와 repo-server가 늘어난다(추정). P2에서 다시 잰다.

## 노드 크기 산정(추정)

| 항목 | 메모리 |
| --- | --- |
| monitoring | 1.4Gi |
| aiops(워커 2개 모델 적재) | 1.5Gi |
| shopapi(공통, 고객사 2) | 0.2Gi |
| argocd(앱 붙인 뒤 여유 포함) | 0.3Gi |
| EKS 애드온(VPC CNI, kube-proxy, CoreDNS, EBS CSI, Pod Identity Agent, External Secrets) | 0.5Gi(측정 안 함) |
| 합계 | 약 3.9Gi |

- 메모리만 보면 4GiB 노드 2대(Pod에 줄 수 있는 양은 노드당 약 3.3~3.4Gi)에서 약 60%다. 롤링 업데이트 겹침과 워커 limit까지 생각하면 여유가 크지 않다.
- **Pod 수가 먼저 걸린다.** 로컬에서 ArgoCD까지 올렸을 때 Running Pod가 34개였다(headlamp, registry 포함). EKS는 여기에 DaemonSet(aws-node, kube-proxy, ebs-csi-node, eks-pod-identity-agent)이 노드마다 붙는다. VPC CNI 기본 설정에서 t4g.medium은 노드당 Pod 17개, t4g.large는 35개다(AWS ENI 표 기준, 측정 아님). t4g.medium 2대는 34개라 모자란다.
- 선택지는 둘이다. (가) t4g.large 2대. 메모리 16GiB, Pod 70개로 여유가 크다. (나) t4g.medium 2대 + VPC CNI prefix delegation. Pod 수 제한은 풀리지만 메모리 여유가 약 40%다. 실험 측정이 메모리 압박으로 흔들리지 않는 쪽은 (가)다. 시간당 비용 차이는 P1b에서 요금 계산기로 확인한다.

## 다시 띄운 클러스터에서 확인한 것

- `run_cluster`의 `restore()`는 overlay를 커밋된 태그로 되돌린다. 레지스트리를 새로 만들면 그 태그 이미지가 없어서 정리 단계가 `ImagePullBackOff`로 멈춘다. 이번에는 코드가 같음을 `git diff 1ec6b58 HEAD -- app targets Dockerfile requirements.txt`(빈 결과)로 확인하고 같은 이미지를 `base-1ec6b58`로도 올렸다. 클러스터를 새로 만들면 `build_push.sh` 뒤 overlay 태그 변경을 커밋하거나 커밋된 태그를 레지스트리에 올린다.
