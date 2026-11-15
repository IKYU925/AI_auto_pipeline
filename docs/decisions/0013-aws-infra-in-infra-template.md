# 0013. 4단계 AWS 리소스는 infra_template 저장소에서 만들고 지운다

상태: 채택
날짜: 2026-11-15

## 맥락

plan.md 10장은 4단계 Terraform을 이 저장소의 `deploy/aws/`에 두려고 했다. 사용자는 이미 별도 인프라 저장소 infra_template(Terraform bootstrap, OIDC, Ansible, CI 스캔)을 운영하고 있고, 자동화 저장소와 인프라 정의가 섞이지 않기를 원했다.

## 결정

4단계 AWS 리소스(EC2, CloudWatch Agent 설정, 경보, EventBridge, SQS, 자동화용 IAM 사용자, ECR, Budgets)는 infra_template의 `terraform/envs/aiops-lab`에서 만들고 지운다. 이 저장소에는 리소스 정의를 두지 않고 `deploy/aws/`도 만들지 않는다. 이 저장소는 그 출력값을 환경 변수로만 받는다.

| Terraform output | 환경 변수 |
| --- | --- |
| `region` | `AWS_REGION` |
| `sqs_queue_url` | `AIOPS_SQS_QUEUE_URL` |
| `log_group_name` | `AIOPS_LOG_GROUP` |
| `automation_user_name` | 그 사용자의 키를 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`로(`.env`에만) |
| `ecr_repository_url` | `ECR_REPOSITORY_URL`(`scripts/build_push.sh`) |

## 근거

- 실무에서 MSP는 고객 인프라 코드와 운영 자동화를 다른 저장소와 다른 권한으로 관리한다. 자동화는 읽기 권한(SQS 읽기와 삭제, Logs 조회)만 가진다.
- infra_template에 이미 상태 저장소(S3, DynamoDB), OIDC, CI 스캔이 있다. 같은 것을 이 저장소에 다시 만들 이유가 없다.
- 두 저장소 사이의 계약이 경보 이름 규칙, 이벤트 모양, 출력값 다섯 개로 좁다. 어느 쪽이 바뀌어도 고칠 곳이 분명하다.

## 영향

- 계약: 경보 이름 `<tenant>-<service>-<alertname>`(하이픈은 고객사 이름에만), 메시지 본문은 EventBridge 이벤트 JSON(SNS 봉투 없음), 태그 키 `Project=ai-incident`(대문자 P).
- shopapi 실행 방식(`deploy/k8s/shopapi/shopapi.yaml`의 명령, 포트 8100, `PG_API_KEY`)을 바꾸면 infra_template의 `lab.sh deploy`도 같이 바꿔야 한다.
- 이 저장소에 Terraform, CloudFormation, 리소스를 만드는 boto3 호출을 넣지 않는다.

## 검토한 대안

- 이 저장소의 `deploy/aws/`: 처음 계획. 인프라 정의와 자동화 코드가 섞이고, infra_template의 bootstrap과 CI를 다시 만들어야 한다.
- infra_template의 기존 `dev` 환경에 옵션 추가: `dev`는 NAT 게이트웨이, RDS, 프라이빗 서브넷이 기본이라 4단계 비용 규칙(NAT와 로드밸런서 없음)과 맞지 않는다.
