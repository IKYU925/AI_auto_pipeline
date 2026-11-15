# F07. SQS 어댑터(EC2, CloudWatch)

상태: 구현 중(코드와 단위 테스트 완료, AWS 계정 첫 실행 전)
관련: plan.md 10장, 4-1, 결정 [0002](../decisions/0002-sqs-intake.md), [0013](../decisions/0013-aws-infra-in-infra-template.md)

## 문제

4단계에서는 EC2의 shopapi 장애를 CloudWatch 경보로 감지한다. 자동화의 입구는 지금 Alertmanager 웹훅과 n8n API뿐이라 CloudWatch 경보를 받을 수 없다.

## 목표와 비목표

- 목표: 경보 상태 변경 이벤트(EventBridge -> SQS)를 기존 접수 경로(`IncidentIn`, 중복 제거, Redis 큐)에 태운다. 자동화 본체(그래프, 워커, 회신)는 고치지 않는다.
- 목표: 로그 경보(S18)는 CloudWatch Logs Insights로 스택트레이스를 붙이고, 지표 경보(S19)는 문구만으로 접수한다.
- 비목표: AWS 리소스 만들기. infra_template의 `terraform/envs/aiops-lab`이 만든다(결정 0013).
- 비목표: EC2 자동 조치. 4단계는 런북 안내만 한다.

## 사용 시나리오

1. tenant-a의 EC2 shopapi가 ERROR 로그를 남기면 경보 `tenant-a-shopapi-ErrorLogs`가 울린다.
2. 자동화의 SQS 소비자가 이벤트를 받아 장애로 접수하고, 스택트레이스를 조회해 붙인 뒤 메시지를 지운다.
3. 워커가 처리해 `#tenant-a-ops`에 회신한다.
4. 경보가 `OK`로 돌아오면 같은 장애에 경보 해소 시각이 남는다.

## 요구사항

- 설정(값은 infra_template의 terraform output): `AIOPS_SQS_QUEUE_URL`, `AIOPS_LOG_GROUP`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`. 큐 URL이 비면 소비자를 띄우지 않는다. 키는 `.env`나 k8s Secret에만 둔다.
- 메시지 본문은 EventBridge 이벤트 JSON 그대로다(SNS 봉투 없음). 예시는 `tests/fixtures/cloudwatch_alarm_*.json`.
- 경보 이름은 `<tenant>-<service>-<alertname>`이고 `rsplit("-", 2)`로 나눈다.
- 상태별 처리
  - `ALARM`: 같은 경보의 열린 장애가 있으면 넘어가고, 없으면 접수한다.
  - `OK`: 열린 장애의 `alert_resolved_at`을 남긴다. 접수한 적 없는 경보면 아무것도 하지 않는다.
  - `INSUFFICIENT_DATA`: 무시한다.
- 처리를 마친 메시지만 지운다. 처리 중 예외(DB, Redis 장애)가 나면 지우지 않아서 visibility timeout(300초) 뒤 다시 받는다. 파싱할 수 없는 메시지는 다시 받아도 실패하므로 지운다.
- 호출하는 AWS API는 IAM 허용 목록 안에 있다. SQS `ReceiveMessage`, `DeleteMessage`와 Logs `StartQuery`, `GetQueryResults`, `StopQuery`다.

## 완료 기준

- [x] ALARM 접수, OK 해소, 하이픈 고객사 파싱, 접수 이력 없는 OK와 INSUFFICIENT_DATA 무시(`tests/test_sqs_intake.py`)
- [x] 접수 중 예외가 나면 메시지를 지우지 않는다(테스트)
- [x] 지표 경보는 Logs 조회 없이 접수한다(테스트)
- [ ] 실제 계정에서 경보 상태 변경이 EventBridge를 거쳐 SQS로 들어오고, S18이 `#tenant-a-ops`에 자동 수정 회신으로 끝난다
- [ ] S19가 코드 수정 없이 런북 안내로 끝난다
- [ ] `terraform destroy` 뒤 `lab.sh leftovers`가 0개

## 설계 메모

- **소비자는 API 프로세스 안의 스레드다.** 큐 URL이 있을 때만 FastAPI lifespan에서 띄운다. 처리는 접수(Redis 적재)까지라 몇 초면 끝나고, LLM 처리는 기존 워커가 한다. 따로 Deployment를 만들지 않아서 1~3단계 매니페스트가 그대로다.
- **ChangeMessageVisibility로 연장하지 않는다.** 한 번에 1개씩 받고 Logs Insights 조회를 30초로 끊으므로 300초 안에 지운다. IAM에는 권한이 있어서 처리 시간이 길어지면 그때 쓴다.
- **지문은 경보 이름으로 정한 첫 줄만 쓴다.** CloudWatch의 `reason`은 이전 상태와 측정값에 따라 문구가 달라서 메시지 둘째 줄에 둔다. 지문이 흔들리면 반복 경보가 매번 새 장애가 된다.
- **`alert_fingerprint`는 `cloudwatch:<경보 이름>`이다.** Alertmanager 지문과 겹치지 않게 접두어를 붙였다.
- **S18은 S01 패치를 그대로 쓴다.** `scenarios/S18.patch`는 `S01.patch`를 가리키는 심볼릭 링크다.
- **S19는 디스크를 df로 재서 채운다.** 루트 20GiB에서 이미지와 OS가 차지한 양이 배포마다 달라서, 고정 크기 대신 목표 비율(93%)까지 남은 양을 계산해 `fallocate`한다.

## 열린 질문

- 실제 계정의 이벤트가 fixture와 같은 모양인지(floci는 경보 상태 변경 이벤트를 만들지 못해 같은 모양을 직접 넣어 확인했다).
- `DiskUsageHigh`의 `fstype="xfs"` 차원이 AL2023 실제 지표와 맞는지. 다르면 infra_template에서 고친다.

## 변경 이력

- 2026-11-15 작성. infra_template 세션의 전달사항(PR IKYU925/infra_template#15)을 바탕으로 했다.
