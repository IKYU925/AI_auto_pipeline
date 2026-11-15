# 0014. 4단계 AWS 환경은 EKS로 하고 0001, 0002를 대체한다

상태: 채택
날짜: 2026-11-15

## 맥락

[0001](0001-no-eks.md)은 4단계에서 EKS를 빼고 EC2 1대와 CloudWatch 경로만 다루기로 했다. [0002](0002-sqs-intake.md)는 그 경보를 EventBridge와 SQS로 받기로 했다. PR #42에서 SQS 입구와 EC2 시나리오 두 개를 만들었고, AWS 리소스를 infra_template에 두는 결정을 0013으로 함께 올렸다. PR #42는 병합하지 않고 닫았으므로 0013은 main에 들어간 적이 없다. infra_template의 EC2 환경(aiops-lab)도 실제 계정에 apply한 적이 없어서 지울 클라우드 리소스는 없다.

EC2 경로로는 Pod 장애, 배포 이력, 롤백처럼 MSP가 쿠버네티스 고객 환경에서 실제로 다루는 일을 보일 수 없다. 그래서 AWS 환경을 EKS 클러스터 하나로 바꾼다.

## 결정

- 4단계 AWS 환경은 EKS 클러스터 하나다. 자동화(api, worker, Postgres, Redis), shopapi 고객사, 관측 도구가 모두 그 안에서 돈다.
- 알림 입구는 클러스터 안의 Alertmanager 웹훅 하나다. SQS 입구는 만들지 않는다.
- 클러스터 안의 상태는 ArgoCD(app-of-apps)가 이 저장소의 `deploy/`를 기준으로 맞춘다. 로컬 K3s와 같은 매니페스트를 쓰고, 차이는 `deploy/k8s/overlays/eks`에만 둔다.
- AWS 리소스(VPC, EKS, 노드그룹, IAM, ECR, S3, Secrets Manager 항목)는 infra_template의 `envs/aiops-eks`가 만들고 지운다. 이 저장소에는 `deploy/aws/`를 만들지 않는다.
- 비밀은 Secrets Manager와 External Secrets로, 권한은 Pod Identity로 다룬다.
- 로컬 K3s는 계속 기능 개발과 1차 확인을 맡는다. EKS에는 로컬에서 확인된 것만 올린다.

## 근거

0001의 근거 두 가지에 답한다.

**"EKS는 3단계와 같은 구조라 새로 검증되는 것이 적다."** EKS에서 새로 검증하는 것은 다음과 같다.

- ArgoCD 배포 이력을 분석 컨텍스트로 쓰는 것과 승인 후 롤백. S20(배포 직후 장애), S21(잘못된 이미지 태그)로 확인한다. 로컬 K3s에는 GitOps 배포 이력이 없다.
- Pod Identity와 External Secrets로 비밀과 권한을 다루는 것. 로컬 K3s는 k8s Secret을 스크립트로 만든다.
- 관리형 노드그룹의 노드 교체를 포함한 노드 장애(S16의 EKS판). 로컬은 VM을 멈췄다 다시 켜고, EKS는 노드그룹이 새 노드로 교체한다.
- 로컬 K3s와 EKS를 같은 매니페스트로 돌려 감지 시간과 처리 시간을 비교하는 것.

**"EKS는 시간당 비용이 생긴다."** 클러스터를 상시 운영하지 않는다. 실험 세션마다 만들고 지운다. 삭제 순서(ArgoCD 앱 삭제, PVC와 Service 정리 대기, `terraform destroy`)와 남은 리소스 검사(`Project=ai-incident` 태그와 클러스터 태그)를 코드로 둔다. LoadBalancer 서비스는 만들지 않는다.

## 영향

- 0001과 0002는 이 기록으로 대체된다.
- plan.md 1장 목표, 2장 일정(4-1~4-5), 10장, 11장 리스크, 12장 결과 표를 EKS 기준으로 바꿨다. 일정은 설계 문서(infra_template `docs/plans/aiops-eks-design.md`) 6장의 P0~P6을 따르고, 이 저장소 몫은 P2~P6이다.
- 시나리오: EC2 시나리오(PR #42)는 만들지 않는다. S20, S21, S22(선택)를 더한다.
- 승인 조치: EKS 관리형 노드에는 SSH가 없어서 노드 대상 Ansible 조치([0003](0003-approved-remediation.md))를 EKS에서 쓸 수 없다. EKS의 허용 목록은 `argocd-rollback`(직전 정상 리비전)과 `rollout-restart`다. 조치 이름과 대상은 장애 기록에서만 가져오고 LLM 출력은 쓰지 않는다. 0003의 노드 Ansible 조치와 S17은 로컬 K3s 전용으로 남긴다. 승인 버튼 클릭은 Slack Socket Mode로 받아 공개 주소 없이 로컬과 EKS가 같은 방식을 쓴다.
- `scripts/build_push.sh`의 ECR 푸시 옵션은 EKS에서도 쓰므로 PR #42에서 옮겨 왔다.
- SQS 입구 코드는 PR #42의 브랜치(`feature/4-0-sqs-intake`) 이력에 남아 있다.

## 검토한 대안

- 0001대로 EC2와 CloudWatch: 쿠버네티스 없이 EC2 장애만 다뤄서 Pod 장애, GitOps 배포 이력, 롤백을 다룰 수 없다. SQS 입구는 동작을 확인했지만 쓰지 않는 입구를 유지하는 비용이 더 크다.
- EKS와 EC2 경로를 모두 유지: 입구가 두 개가 되고 두 저장소가 쓰지 않는 Terraform 환경과 어댑터를 계속 유지해야 한다.
- 자동화는 로컬에 두고 EKS 알림을 SNS와 SQS로 받기: 자동화가 클러스터 밖에 있어서 중간 단계와 지연만 늘어난다.
