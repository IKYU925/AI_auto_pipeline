# F15. EKS GitOps 배포와 기존 경로 재현

상태: 완료
관련: plan.md 10장(P2, P3), 4-1, 4-2, 결정 0014

## 문제

3단계 자동화는 로컬 K3s에서만 돈다. 실제 운영 환경과 같은 관리형 Kubernetes(EKS)에서도 같은 매니페스트와 같은 이미지로 감지부터 회신까지 이어지는지 확인한 적이 없다. 클러스터를 손으로 올리면 재현할 수 없고, 계속 켜 두면 예산(100~120달러)을 넘는다.

## 목표와 비목표

- 목표: 빈 EKS 클러스터에서 Git만으로 자동화, 고객사 shopapi, 관측 도구가 모두 Healthy가 된다.
- 목표: S01(코드 버그)과 S14(설정 오류)가 EKS에서 감지부터 Slack 회신까지 로컬과 같은 결과로 끝난다.
- 목표: 클러스터는 실험할 때만 만들고, 지운 뒤 과금 리소스가 0개임을 확인한다.
- 비목표: 인그레스와 외부 공개. 접속은 port-forward다. LoadBalancer는 만들지 않는다(삭제 누락과 비용 위험).
- 비목표: 고가용성, 다중 AZ 노드 그룹, 클러스터 상시 운영.

## 운영자 시나리오

1. infra_template에서 `make eks-up`을 실행한다. Terraform이 클러스터와 노드, ArgoCD, 루트 Application 하나를 만든다.
2. 루트 앱이 이 저장소의 `deploy/argocd`(Helm 차트)를 읽어 앱 10개를 sync wave 순서로 만든다. 운영자는 기다리기만 한다.
3. 지식 문서 수집을 한 번 실행하고, port-forward로 실험한다.
4. 끝나면 `make eks-down`. 루트 앱을 지워 하위 리소스를 정리한 뒤 Terraform destroy, 남은 리소스 검사까지 한다.

## 요구사항

- 앱 목록과 sync wave는 `deploy/argocd/values.yaml`의 `apps` 표 하나에 둔다. 순서는 External Secrets(-2), 클러스터 설정(-1), 관측(0), 자동화(1), 고객사 shopapi(2)다.
- 자동화와 shopapi는 3단계와 같은 kustomize base를 쓰고, `deploy/k8s/overlays/eks`는 Ingress 삭제, 비밀 연동, ArgoCD 권한만 더한다.
- 이미지 태그는 `values.yaml`의 `imageTag` 한 줄이 정한다. Terraform 변수로 덮을 수 있지만 기본은 Git 값이다.
- 비밀은 Secrets Manager `ai-incident/aiops` 하나에서 ExternalSecret으로 받는다.
- 모든 앱은 자동 sync에 selfHeal을 끈다. 장애 주입과 승인 후 롤백이 Application이나 Deployment를 바꾸기 때문이다. 루트 앱도 같다(infra_template #32).
- 실행기(`scripts/run_cluster.py`)는 `CLUSTER=eks`면 shopapi 버그 이미지를 ArgoCD Application 이미지로 배포하고, 끝나면 Git 기준으로 되돌린다.

## 완료 기준

- [x] 빈 클러스터에서 손으로 바꾼 값 없이 앱 10개 Synced/Healthy(241초, 334초)
- [x] S14, S01이 EKS에서 감지부터 회신까지 로컬과 같은 결과(S14 no_code_fix 186.9초, S01 resolved 110.7초)
- [x] 세션마다 `eks-down` 뒤 `leftovers: 0`(3회)
- [x] 결과 기록([results/phase4_eks.md](../../results/phase4_eks.md))

## 설계 메모

- **Terraform은 ArgoCD와 루트 앱만 만든다.** 앱 목록을 Terraform에 두면 앱을 더할 때마다 인프라 저장소 PR이 필요하다. 이 저장소의 Helm 차트가 앱 목록을 갖고, Terraform은 ECR 주소와 Loki 버킷 같은 인프라 값만 넘긴다.
- **shopapi를 고객사마다 Application으로 나눴다.** 처음에는 자동화 앱 하나에 shopapi 세 벌이 같이 있었다. 고객사 하나만 롤백하려면 앱이 나뉘어야 해서 4-4에서 나눴다(F17).
- **base 층과 cluster 층을 나눴다.** ECR, S3, Secrets Manager는 base 층으로 계속 두고(월 약 0.63달러), 과금이 큰 클러스터만 세션마다 만들고 지운다. 코어타임 밖에서는 매시 정리 워크플로가 지운다.
- **Grafana 데이터 소스도 Git만으로 생긴다.** 처음에는 DB 사용자를 만드는 명령형 단계라 `scripts/k8s_grafana.sh`로 손으로 돌렸다. 지금은 api Pod의 `grafana-db` init 컨테이너(`app/grafana_db.py`)가 뜰 때마다 `grafana_ro`와 데이터 소스 Secret을 맞춘다. 비밀번호는 클러스터 안에서 만들어 `monitoring/aiops-postgres-datasource`에만 둔다. Secrets Manager 키를 더하지 않는다.

## 열린 질문

- 실험 중 `main`에 병합하면 루트 앱이 다시 sync해 장애 주입이 풀린다(트러블슈팅 #30). 루트 앱을 실험 시작 커밋에 고정하는 방법(`argocd_root_revision`)은 아직 쓰지 않았다.
- 실험 기간 AWS 비용은 Cost Explorer 반영 뒤 기록한다.

## 변경 이력

- 2026-11-17 작성. 4-1, 4-2는 PR #58에서 끝났고, 이 문서는 구현 뒤에 정리했다.
- 2026-11-23 Grafana 데이터 소스를 `scripts/k8s_grafana.sh`에서 api Pod의 `grafana-db` init 컨테이너로 옮겼다. 새 클러스터에서 손으로 할 단계가 하나 줄었다.
