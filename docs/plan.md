# AI 장애 대응 자동화 구축 및 검증 계획 (개정판)

작성일: 2026-11-13, 개정일: 2026-11-14

이 문서는 전체 목표, 단계, 작업 순서를 다룬다. 기능별 요구사항과 완료 기준은 [features](features/README.md)에, 계획을 바꾼 결정과 그 이유는 [decisions](decisions/README.md)에 둔다.

### 1. 목표

로컬 쿠버네티스 또는 AWS(EC2)에서 운영 중인 서비스에 장애가 나면, 모니터링이 감지해 알림을 보내고 자동화가 원인 분석, 수정안 생성과 검증, 런북 안내, Slack 보고까지 이어서 처리하는 구조를 만든다. 자동화 본체(접수, 큐, 분석, 검증, 보고)는 운영 환경과 무관하게 하나로 두고, 환경마다 달라지는 부분은 알림 입구, 로그 조회, 배포 방식 세 곳으로 제한한다.

이 구조를 단계별로 검증한다. 현재 워크플로우를 실제 장애 시나리오로 돌려 결과를 숫자로 남기고, 저장소를 PostgreSQL로 옮겨 전후를 비교한다. 그다음 관측 도구를 붙여 모니터링이 장애를 감지하면 자동화가 스스로 시작되게 만들고, 같은 구조를 AWS에서 다시 확인한다.

- 1단계: 현재 스택(Redis, Qdrant)으로 실제 LLM을 호출해 지표를 확보한다.
- 2단계: PostgreSQL + pgvector로 옮기고, 이력 보존과 중간 재개 효과를 측정한다.
- 3단계: 로컬 Vagrant K3s 3노드 클러스터에 대상 서비스와 자동화 시스템을 배포하고, Prometheus, Loki, Alertmanager, Grafana로 감지부터 대응, 보고까지 사람 개입 없이 이어지게 한다. Kubernetes 장애(CrashLoop, OOMKilled, 노드 장애, 노드 디스크 부족)도 다루고, 감지 시간과 전체 처리 시간을 측정한다. 여기에 MSP 운영 방식을 더한다. 고객사 두 곳을 namespace로 나눠 고객사별로 알림과 보고를 분리하고, 사람이 승인하면 허용된 Ansible 플레이북으로 조치까지 실행한다(9-7, 9-8).
- 4단계: AWS EC2에서 난 장애를 CloudWatch로 감지하고 EventBridge와 SQS를 거쳐 로컬의 같은 자동화가 처리하는지 확인한다. 바뀌는 것은 감지 수단과 알림 입구뿐이고, 자동화 본체와 분석, 보고는 3단계 그대로다(10장).

결과물은 다음과 같다.

- 시나리오별 실험 원본 데이터(CSV)와 요약 표
- README 결과 섹션 갱신
- Slack 회신 스크린샷, Grafana 대시보드 스크린샷, 시연 영상(2~3분)
- velog 회고 글(단계별 1편, 총 4편)

이번 계획에서 하지 않는 것은 다음과 같다.

- 실제 사용자 트래픽이 있는 서비스에는 연결하지 않는다. 운영 환경과 같은 구조(K3s, EC2) 위에 직접 만든 대상 서비스를 올려 검증한다.
- 패치를 자동으로 머지하거나 배포하지 않는다.
- Python 외 언어는 지원하지 않는다.
- 사람 승인 없이 조치하지 않는다. 조치는 3단계 로컬 노드에서 허용 목록에 있는 Ansible 플레이북(로그와 임시 파일 정리, 서비스 재시작)만 승인 후 실행한다. 쿠버네티스 객체 변경(재시작, 스케일 조정)과 AWS 리소스 조치는 하지 않는다.

### 2. 작업 순서

단계를 섞지 않는다. 1단계 결과가 있어야 2단계에서 무엇이 좋아졌는지 비교할 수 있고, 2단계의 PostgreSQL 데이터와 체크포인터가 있어야 3단계에서 자동화 현황 대시보드와 Pod 종료 중 재개를 보여줄 수 있다. 1, 2단계는 docker compose에서, 3단계는 로컬 K3s에서, 4단계는 AWS에서 진행한다. 4단계는 3단계에서 감지부터 회신까지 완주한 뒤에만 시작한다. 로컬에서 안 되는 경로를 클라우드에서 고치면 비용만 든다.

3단계의 클러스터 준비 중 상당 부분은 infra_local_set에 이미 들어가 있다. 워커 노드 메모리 4GB 증설, 배치용 노드 라벨, 로컬 레지스트리, 관측 도구(kube-prometheus-stack, Loki, Alloy) 설치, Alertmanager 웹훅 수신처와 Bearer 토큰 설정이 끝났다. 그래서 줄어든 작업과 4단계를 줄여 생긴 여유를 3단계의 고객사 분리와 승인 조치(3-9, 3-10)에 쓴다.

작업 ID `2-3`은 2단계의 세 번째 작업이라는 뜻이다. 순서만 나타내고 기간을 약속하지 않는다. 실제 걸린 시간은 결과 문서에 적는다.

| 작업 | 단계 | 내용 | 완료 기준 |
| --- | --- | --- | --- |
| 1-1 | 1단계 | 대상 서비스 shopapi 구축, 테스트 작성, 워커 연결 | S01 시나리오 1건이 실제 LLM으로 끝까지 실행됨 |
| 1-2 | 1단계 | 장애 시나리오 13개 작성, 시나리오별 정답과 숨은 테스트 정의 | 시나리오마다 버그 주입 후 숨은 테스트가 실패함을 확인 |
| 1-3 | 1단계 | 실험 스크립트, 지표 수집, 최적화 끄기 옵션 구현 | 시나리오 1개를 스크립트로 실행하면 CSV 한 줄이 나옴 |
| 1-4 | 1단계 | Slack 실연동(테스트 채널, ngrok, n8n, 봇 회신) | 실제 Slack 알림 3건이 스레드 회신까지 완주 |
| 1-5 | 1단계 | 본 실험 A(최적화 켬), 전 시나리오 3회 반복 | phase1_A.csv 완성 |
| 1-6 | 1단계 | 본 실험 B(최적화 끔), 결과 분석 | phase1_B.csv, 비교 요약 완성 |
| 1-7 | 1단계 | 정리(README 결과 표, velog 초안, 영상) | 1단계 결과 공개 가능 |
| 2-1 | 2단계 | 스키마 설계, PostgreSQL(pgvector) 컨테이너, Alembic 마이그레이션 | 테이블 생성, 확장 활성화 확인 |
| 2-2 | 2단계 | 상태, 비용, 수정안 저장을 PostgreSQL로 이전 | 기존 테스트 전부 통과 |
| 2-3 | 2단계 | pgvector 검색 + pg_trgm 하이브리드 검색(RRF) | 검색 품질 비교 데이터 확보 |
| 2-4 | 2단계 | LangGraph PostgreSQL 체크포인터, 멱등성 처리 | 워커를 죽여도 중간부터 재개 |
| 2-5 | 2단계 | 장애 상황 실험(워커 강제 종료, 재시작 후 이력 확인) | 재개 실험 결과 기록 |
| 2-6 | 2단계 | 1단계와 같은 시나리오 재실행, 전후 비교 | phase2.csv, 전후 비교 표 완성 |
| 2-7 | 2단계 | 정리(README, velog 2편, 영상) | 2단계 결과 공개 가능 |
| 3-1 | 3단계 | `./up.sh`로 클러스터 점검, 빌드 스크립트(`scripts/build_push.sh`), 이미지 등록 | 세 노드 Ready, 레지스트리에서 이미지 pull 성공, Grafana에서 클러스터 지표가 보임 |
| 3-2 | 3단계 | 자동화 시스템과 shopapi 매니페스트(kustomize), Secret, PVC, Ingress | 모든 Pod Running, Ingress로 API 접근 |
| 3-3 | 3단계 | 클러스터에서 S01 실행(API 직접 호출), shopapi와 자동화의 `/metrics`, JSON 로그, ServiceMonitor | 1, 2단계와 같은 결과가 K3s에서도 나옴. Grafana에서 shopapi 메트릭과 로그가 보임 |
| 3-4 | 3단계 | PrometheusRule, `loki-alert-rules` ConfigMap, infra_local_set의 Alertmanager 라우팅 조정(9-3) | 버그 주입 시 Alertmanager에 알림이 1건 뜨고 `ai-automation` 수신처로 감 |
| 3-5 | 3단계 | Alertmanager 웹훅 어댑터, Loki 스택트레이스 조회, K8s 런북 추가 | 알림 1건이 사람 개입 없이 Slack 회신까지 완주 |
| 3-6 | 3단계 | 자동화 메트릭, Grafana PostgreSQL 데이터 소스, 대시보드 2개 | 대시보드에서 장애 수, 성공률, 비용, 큐 길이가 보임 |
| 3-7 | 3단계 | K8s 장애 시나리오(S14~S16) 작성, 실제 감지 경로로 전체 시나리오 실행 | 감지 시간, 전체 처리 시간 측정 완료 |
| 3-8 | 3단계 | 알림 묶기, 비코드 알림 분류, Pod 종료 중 재개 실험, 결과 분석 | phase3.csv, 요약 완성 |
| 3-9 | 3단계 | 고객사 namespace 2개(tenant-a, tenant-b), 고객사 라벨과 Alertmanager 라우팅, 고객사별 Slack 채널과 런북 필터(9-7) | 같은 버그를 두 고객사에 주입하면 각 고객사 채널로 1건씩 회신 |
| 3-10 | 3단계 | 승인 조치 Job과 허용 목록 플레이북, Slack 승인 버튼, 조치 기록, S17 노드 디스크 부족(9-8) | S17 알림이 승인 후 플레이북 실행, 알림 해소까지 사람 개입은 승인 1번 |
| 3-11 | 3단계 | 정리(README, 아키텍처 그림 갱신, velog 3편, 영상) | 3단계 결과 공개 가능 |
| 4-1 | 4단계 | Terraform으로 EC2 1대, CloudWatch Agent 설정, 경보, EventBridge 규칙, SQS 생성. SQS 어댑터, S18, S19 실행 | 경보 1건이 사람 개입 없이 Slack 회신까지 완주 |
| 4-2 | 4단계 | 정리(README, 환경별 비교 표, velog 4편), `terraform destroy` 후 남은 리소스 확인 | 4단계 결과 공개 가능, 과금 리소스 0개 |

작업이 밀리면 측정, Slack 연동, 영상 순으로 우선순위를 둔다. 측정 결과가 없으면 나머지는 의미가 없다. 3단계가 밀리면 대시보드는 shopapi 하나로 줄이고, S16(노드 장애)은 뒤로 미루고, K3s 위에서 감지부터 회신까지 완주하는 것을 우선한다. 3단계 추가 작업이 밀리면 승인 조치를 먼저 하고 고객사 분리는 라우팅만 남긴다. 4단계가 밀리면 S19를 빼고 S18 한 건으로 경로 완주만 확인한다.

### 3. 대상 서비스 설계 (1단계)

`sample_repo` 대신 실제로 동작하는 작은 FastAPI 서비스를 만든다. 워커 이미지에 이미 FastAPI, pydantic, httpx, pytest가 있어서 같은 이미지로 대상 서비스 테스트를 돌릴 수 있다.

폴더 구조는 다음과 같다.

```
AI_auto_pipeline/
├── targets/
│   └── shopapi/
│       ├── shopapi/
│       │   ├── main.py          # FastAPI 앱, 예외 핸들러(Slack 알림 전송)
│       │   ├── orders.py        # 주문 합계, 평균 단가
│       │   ├── pricing.py       # 할인, 쿠폰, 세금
│       │   ├── inventory.py     # 재고 차감
│       │   ├── payments.py      # 외부 PG 호출(가짜 서버)
│       │   ├── pagination.py    # 목록 조회 페이지 계산
│       │   └── config.py        # 환경변수
│       ├── tests/               # 모듈별 테스트, 총 30개 내외
│       └── pytest.ini
├── scenarios/
│   ├── S01.patch               # 정상 코드에 버그를 넣는 패치
│   ├── S01.yaml                # 시나리오 정의(정답, 트리거, 숨은 테스트)
│   └── hidden_tests/           # LLM이 모르는 검증용 테스트
├── knowledge/
│   ├── shopapi/                # shopapi 런북 3~5개
│   └── k8s/                    # CrashLoopBackOff, OOMKilled, NodeNotReady 런북(3단계)
├── deploy/k8s/                 # kustomize base, overlays/local(3단계)
├── deploy/ansible/             # 승인 조치용 허용 목록 플레이북(3단계)
├── deploy/aws/                 # Terraform: EC2, CloudWatch Agent 설정, 경보, EventBridge, SQS(4단계)
├── scripts/
│   ├── inject.sh               # 버그 주입, 원복
│   ├── build_push.sh           # 이미지 빌드와 레지스트리 등록(3단계)
│   └── run_experiment.py       # 시나리오 실행, 지표 수집
└── results/                    # 실험 원본 CSV, 요약
```

시나리오 정의 파일 예시는 다음과 같다.

```yaml
id: S01
title: 빈 주문의 평균 단가 계산 시 0으로 나누기
type: code_bug              # code_bug | infra | config
difficulty: easy            # easy | medium | hard
expected:
  category: code_bug
  needs_code_fix: true
  root_cause_file: shopapi/orders.py
  outcome: resolved         # resolved | no_code_fix | escalated
requests:                   # 실험 스크립트가 버그가 든 shopapi에 순서대로 보내 실제 스택트레이스를 얻는다
  - method: POST
    path: /orders/summary
    json: {items: []}
hidden_test: hidden_tests/test_s01.py
```

숨은 테스트를 따로 두는 이유는 "선별된 테스트 통과"가 "버그 수정"과 같지 않기 때문이다. 관련 테스트가 없으면 엉뚱한 패치도 통과한다. 그래서 LLM이 볼 수 없는 숨은 테스트를 시나리오마다 하나씩 두고, 수정이 맞았는지는 숨은 테스트와 전체 테스트로 판정한다.

대상 저장소를 바꾸려면 `docker-compose.yml`의 워커 볼륨을 `./targets/shopapi:/repo:ro`로 바꾼다.

### 4. 장애 시나리오

난이도를 섞고, 실패가 예상되는 시나리오도 넣는다. 쉬운 것만 넣으면 직접 출제하고 직접 맞히는 실험이 되어 결과를 믿기 어렵다.

| ID | 유형 | 난이도 | 장애 내용 | 예상 결과 |
| --- | --- | --- | --- | --- |
| S01 | code_bug | easy | 빈 주문 평균 단가 계산 시 ZeroDivisionError | 자동 수정 |
| S02 | code_bug | easy | 쿠폰 없는 주문에서 None 속성 접근(AttributeError) | 자동 수정 |
| S03 | code_bug | easy | 상품 옵션 누락 시 KeyError | 자동 수정 |
| S04 | code_bug | medium | 수량이 문자열로 들어와 형 변환 오류(TypeError) | 자동 수정 |
| S05 | code_bug | medium | 마지막 페이지 계산 오류로 IndexError | 자동 수정 |
| S06 | code_bug | medium | 음수 수량 검증 누락으로 재고 차감 단계에서 ValueError | 자동 수정 |
| S07 | code_bug | medium | 할인 계산을 고치면 다른 테스트가 깨지는 회귀 유발 버그 | 재시도 후 수정 |
| S08 | code_bug | hard | 함수 시그니처 변경이 필요해 여러 모듈에 영향 | 위험도 high, 수정 또는 에스컬레이션 |
| S09 | code_bug | hard | 간헐적으로만 재현되는 순서 의존 버그 | 에스컬레이션 |
| S10 | infra | easy | 외부 PG 응답 지연으로 타임아웃 | 코드 수정 없이 런북 안내 |
| S11 | config | easy | 필수 환경변수 누락 | 코드 수정 없이 런북 안내 |
| S12 | 재발 | easy | 배포 전 S01 재발 | LLM 0회, 검증된 수정안 재사용 |
| S13 | 중복 | easy | S02 알림 10회 연속 발생 | 1건만 처리 |

3단계에서는 S11과 S14가 같은 경로(기동 실패 반복, `KubePodCrashLooping`)로 들어온다. 둘 다 돌리되 결과 표에서는 같은 유형으로 묶는다.

### 5. 실제 알림 연결

1, 2단계의 알림 경로다. 3단계에서는 Slack 메시지 파싱 대신 클러스터 안의 Alertmanager 웹훅이 입구가 된다(9장). 1, 2단계에서는 측정과 실연동 검증을 나눈다.

측정용 경로(1-5~1-6 본 실험)는 실험 스크립트가 FastAPI `/api/v1/incidents`를 직접 호출한다. Slack과 n8n을 거치지 않아서 재현이 쉽고, 시간 측정이 정확하다.

실연동 검증 경로(1-4)는 실제 운영과 같은 흐름이다.

1. shopapi 예외 핸들러가 Slack Incoming Webhook으로 `[shopapi] critical: 메시지` + 스택트레이스 코드 블록을 보낸다.
2. n8n Slack Trigger가 메시지를 받는다. Slack이 이벤트를 보낼 수 있도록 ngrok으로 n8n을 공개 주소에 연결하고 `WEBHOOK_URL`을 설정한다.
3. n8n이 FastAPI로 전달하고, 워커가 처리한다.
4. 워커가 봇 토큰으로 원래 스레드에 결과를 회신한다.

준비할 것은 다음과 같다.

- Slack 앱: Incoming Webhook, 봇 토큰(`chat:write`), Event Subscriptions(`message.channels`), 테스트 채널 초대
- ngrok 계정과 n8n 공개 주소
- `.env`의 `SLACK_BOT_TOKEN`
- n8n에서 워크플로 Import 후 Slack 자격 증명, 채널 지정

### 6. 측정 지표

| 지표 | 정의 | 수집 위치 |
| --- | --- | --- |
| 분류 정확도 | 기대 분류와 일치한 비율 | 결과의 classification |
| 자동 수정 성공률 | code_bug 시나리오 중 숨은 테스트와 전체 테스트를 모두 통과한 비율 | 평가 스크립트 |
| 거짓 통과 | 선별 테스트는 통과했지만 숨은 테스트가 실패한 건수 | 평가 스크립트 |
| 첫 시도 성공률, 평균 시도 횟수 | 수정이 몇 번째 시도에 통과했는지 | 결과의 attempts |
| 처리 시간 | 접수부터 완료까지(중앙값, 최대값) | 상태의 시각, 실험 스크립트 기록 |
| 장애당 LLM 비용, 토큰 | 장애 1건에 든 비용 | `cost:{incident_id}` |
| 모델별 호출 비율 | 경량 모델과 고성능 모델 호출 비율 | `cost:{incident_id}` 의 calls |
| 캐시 적중 | 분류, 검색, 임베딩 캐시 적중 수 | 비용 기록, 검색과 임베딩 통계 |
| 테스트 선별 효과 | 선별 실행 테스트 수와 시간, 전체 실행 대비 비율 | test_selection, test_result |
| 재발 비용 | S12의 LLM 호출 수와 비용 | 비용 기록 |
| 중복 제거 | S13에서 접수 10건 대비 처리 건수 | 큐 길이, 상태 |

실험 전에 Anthropic 요금표에서 실제 단가를 확인해 `MODEL_PRICES`를 고친다. 지금 값은 예시라서 그대로 두면 비용 지표가 틀린다.

### 7. 실험 절차

최적화를 켠 조건과 끈 조건을 비교한다. 그래야 "비용 최적화 효과"를 숫자로 말할 수 있다.

| 항목 | 조건 A(최적화 켬) | 조건 B(최적화 끔) |
| --- | --- | --- |
| 분류 캐시 | 사용 | 사용 안 함 |
| 모델 분리 | 분류는 경량, 분석과 수정은 고성능 | 전부 고성능 |
| 조기 종료 | 비코드 장애는 런북 안내로 종료 | 모든 장애에 원인 분석 실행 |
| 테스트 선별 | 영향 범위 기반 선별 | 매번 전체 테스트 |
| 수정안 재사용 | 사용 | 사용 안 함 |

조건 B를 만들려면 환경변수로 각 최적화를 끌 수 있게 해야 한다. 1-3에 `OPT_LLM_CACHE`, `OPT_MODEL_ROUTING`, `OPT_EARLY_EXIT`, `OPT_TEST_SELECTION`, `OPT_FIX_REUSE`를 추가한다.

시나리오 1회 실행 순서는 다음과 같다.

1. 캐시 초기화(S12는 S01 직후 실행하므로 예외)
2. `inject.sh S01`로 버그 주입
3. 트리거 요청으로 장애 발생, 또는 스크립트가 API 직접 호출
4. 상태를 폴링하며 완료 대기(제한 10분)
5. 결과와 비용 수집
6. AI 패치를 복사본에 적용하고 숨은 테스트와 전체 테스트로 판정
7. 버그 원복

LLM 결과는 매번 조금씩 다르므로 시나리오마다 3회 반복하고 평균과 범위를 함께 적는다.

결과를 해석하는 규칙은 실험 전에 정해 둔다.

- A와 B의 차이가 3회 반복의 범위(최소~최대) 안에 들어가면 "차이 없음"으로 적는다.
- A/B 비교는 다섯 가지 최적화를 한꺼번에 켜고 끈 결과다. 개별 최적화의 기여도는 측정하지 않으므로 "모델 분리로 N% 절감"처럼 하나에 돌리지 않는다. 개별 근거로 쓸 수 있는 것은 따로 기록한 지표(모델별 호출 비율, 테스트 선별 시간, S12 재발 비용)뿐이다.
- 매 실행 전에 캐시를 비우므로 분류 캐시는 S12, S13에서만 적중한다. 캐시 효과는 이 두 시나리오의 수치로만 말한다.

예산은 처음 5회 실행의 1건당 평균 비용을 보고 다시 계산한다. 장애당 상한(`INCIDENT_BUDGET_USD`)은 그대로 켜 둔다. 13개 시나리오를 3회씩 두 조건으로 돌리면 약 78회 실행이다.

### 8. 2단계 PostgreSQL 이전 설계

완료. 요구사항과 완료 기준은 [F03](features/F03-postgres.md), 스키마는 [migrations/versions](../migrations/versions/), 이 장과 달라진 결정은 [0012](decisions/0012-postgres-scope.md)에 있다. 결과는 [phase2_summary.md](../results/phase2_summary.md)(1단계 재실행 비교), [phase2_retrieval.md](../results/phase2_retrieval.md)(하이브리드 검색), [phase2_kill.md](../results/phase2_kill.md)(워커 강제 종료)다.

3단계에서 이어 쓰는 설계는 다음과 같다.

- `incidents`에는 3단계용 칼럼을 미리 넣었다. Alertmanager의 `resolved` 알림을 받으면 `alert_fingerprint`로 장애를 찾아 `alert_resolved_at`을 남긴다. `source`(api, slack, alertmanager, cloudwatch)는 자동화 메트릭 `incidents_received_total{source}`와 대시보드 집계에 쓴다.
- 이력성 대시보드(3-6)는 `incidents`, `llm_calls`, `fixes`를 데이터 소스로 쓴다. 분류, 테스트 선별 같은 그래프 결과는 `incidents.result`(jsonb)에 있다.
- 체크포인터에서 재개하면 보고 단계가 다시 돌 수 있다. 회신은 `incidents.report_ts`가 비어 있을 때만 보낸다. 회신과 저장 사이에 워커가 죽으면 회신이 두 번 갈 수 있는데, 이 틈은 없애지 않고 3단계 Pod 종료 실험에서 중복 회신 수를 지표로 기록한다.

### 9. 3단계 로컬 K3s 배포, 관측 도구, 알림 구성

3단계는 맥북의 Vagrant + VirtualBox K3s 3노드 클러스터(컨트롤 플레인 1, 워커 2)에서 진행한다. 클러스터, 레지스트리, 관측 도구는 [infra_local_set](https://github.com/IKYU925/infra_local_set) 레포의 `./up.sh` 한 번으로 구성하고, 앱과 알림 규칙, 대시보드는 이 레포의 `deploy/k8s/`에서 관리한다. 대상 서비스와 자동화 시스템을 모두 K3s에 올리고, 클러스터 안에서 Prometheus, Loki, Alertmanager, Grafana로 감지하고, 감지된 장애가 자동화로 바로 이어지게 한다.

3단계부터는 누군가 알림을 보내 주는 것이 아니라 모니터링이 이상을 감지해서 자동화가 시작된다. 장애는 여전히 직접 주입하지만, 감지부터 회신까지의 흐름은 실제 운영과 같은 구조가 된다. 여기에 Pod 재시작 반복, 메모리 초과, 노드 장애처럼 Kubernetes에서만 생기는 장애도 시나리오에 넣는다.

#### 9-1. 클러스터 구성

```
Vagrant + VirtualBox (맥북)
├── k3s-cp      컨트롤 플레인   : Traefik(Ingress), 로컬 이미지 레지스트리, Headlamp
├── k3s-w1      워커 1 (workload=app)        : namespace shop(shopapi), namespace aiops(api, worker, redis, postgres)
└── k3s-w2      워커 2 (workload=monitoring) : namespace monitoring(Prometheus, Alertmanager, Grafana, Loki, Alloy)
```

| namespace | 리소스 | 비고 |
| --- | --- | --- |
| shop | shopapi Deployment(2개), Service, Ingress | 메모리 limit을 명시해 OOMKilled 시나리오에 사용. Pod 라벨 `app.kubernetes.io/name: shopapi` 필수(9-2) |
| aiops | api Deployment, worker Deployment(2개), redis StatefulSet, postgres(pgvector) StatefulSet, Secret, ConfigMap, PVC | PVC는 K3s 기본 local-path 사용. 모두 `nodeSelector: workload=app` |
| monitoring | kube-prometheus-stack, Loki(single binary), Alloy | infra_local_set의 monitoring 역할이 이미 설치함. 이 레포는 `loki-alert-rules` ConfigMap만 이 namespace에 만든다 |

배포 방식은 다음과 같다.

- 이미지는 맥에서 빌드해 컨트롤 플레인의 로컬 레지스트리(`192.168.56.10:30500`)에 올린다. 노드의 `registries.yaml` 등록은 infra_local_set의 registry 역할이 이미 했다. 맥과 VM이 같은 arm64라서 같은 이미지를 쓴다. 맥 Docker의 `insecure-registries`에 레지스트리 주소를 한 번 등록해야 push할 수 있다.
- 매니페스트는 `deploy/k8s/` 아래 kustomize로 관리한다(base, overlays/local).
- `.env` 값은 Secret(API 키, 토큰)과 ConfigMap(그 밖의 설정)으로 나눈다. `AUTOMATION_TOKEN`에는 infra_local_set 폴더의 `.ai-automation-token` 값을 넣는다.
- api, worker, shopapi는 이미지 하나(`aiops`)를 쓴다. shopapi 소스는 이미지 안 `/repo`에 들어가고, shopapi Pod는 그 소스로 뜨며 워커는 같은 소스를 검증 대상 저장소로 쓴다. 이미지가 하나라야 배포된 shopapi와 워커가 검증하는 코드가 어긋나지 않는다. 버그를 주입하면 이 이미지를 다시 빌드해 배포한다. 태그는 매번 새로 붙인다(`<시나리오>-<git short sha>`, 커밋하지 않은 변경이 있으면 그 diff 해시를 덧붙임, 예: `s01-3f2a1c9-8b1e0d2`). 같은 태그로 다시 올리면 노드에 이미 있는 이미지를 그대로 써서 버그가 주입되지 않는다. `scripts/build_push.sh`가 태그를 만들고 push한 뒤 레지스트리에 올라갔는지 확인한다. overlay 반영은 3-2에서 붙인다.
- 워커 Pod는 Anthropic API와 Slack API로 나가는 외부 통신만 필요하다. Alertmanager 웹훅은 클러스터 내부 주소로 받으므로 ngrok이 필요 없다.

노드 자원은 infra_local_set의 Vagrantfile에 이미 반영되어 있다. VM 3대가 메모리를 약 10GB 쓴다.

| 노드 | vCPU | 메모리 | 주로 올라가는 것 |
| --- | --- | --- | --- |
| k3s-cp | 2 | 2GB | K3s 컨트롤 플레인, Traefik, 레지스트리, Headlamp |
| k3s-w1 | 2 | 4GB | shopapi, api, worker, redis, postgres |
| k3s-w2 | 2 | 4GB | Prometheus, Alertmanager, Grafana, Loki, Alloy |

Prometheus 보관 기간은 이미 1일이고, Loki는 파일시스템 저장소의 single binary 모드다. 메모리가 부족하면 n8n은 3단계에서 배포하지 않는다.

#### 9-2. 관측 도구

infra_local_set이 아래를 설치한다. 차트 버전은 infra_local_set의 `group_vars/all.yml`에 고정되어 있다(kube-prometheus-stack 93.2.2, Loki 7.6.0, Alloy 1.15.0).

| 구성 요소 | 설치 | 역할 |
| --- | --- | --- |
| kube-prometheus-stack | Helm | Prometheus Operator, Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics |
| Loki | Helm, single binary | 로그 저장, ruler로 로그 알림 규칙 평가 |
| Grafana Alloy | Helm, Deployment 1개 | Kubernetes API로 Pod 로그를 읽어 Loki로 전송. Promtail은 지원이 끝나서 Alloy를 쓴다 |

infra_local_set 설정에서 이 레포가 맞춰야 할 점은 다음과 같다.

- Prometheus는 모든 namespace의 ServiceMonitor와 PrometheusRule을 읽는다. 별도 라벨이 필요 없다.
- Alloy는 Pod 라벨 `app.kubernetes.io/name`을 Loki 스트림 라벨 `service`로 옮긴다. 이 라벨이 없으면 로그에 `service`가 비고, 알림 규칙과 Loki 조회가 shopapi 로그를 찾지 못한다.
- Loki 로그 알림 규칙은 `monitoring` namespace의 `loki-alert-rules` ConfigMap으로 읽는다.
- Grafana 대시보드는 `grafana_dashboard` 라벨을 단 ConfigMap을 아무 namespace에 만들면 자동으로 추가된다.
- Grafana 데이터 소스는 Prometheus와 Loki만 등록되어 있다. PostgreSQL 데이터 소스는 이 저장소의 `scripts/k8s_grafana.sh`가 `monitoring` namespace에 라벨 붙은 Secret으로 만든다([F11](features/F11-dashboards.md)).

수집 대상은 다음과 같다.

- shopapi: `prometheus-fastapi-instrumentator`로 `/metrics` 노출, ServiceMonitor로 수집. 로그는 JSON 한 줄로 stdout에 남기고 필드는 `level`, `message`, `path`, `exc_info`(스택트레이스 전체)로 둔다. 서비스 이름은 스트림 라벨 `service`로 이미 붙으므로 JSON에는 넣지 않는다.
- 자동화 api, worker: `prometheus_client`로 `/metrics` 노출, ServiceMonitor로 수집.
- 클러스터: kube-state-metrics(Pod 상태, 재시작 횟수, OOMKilled), node-exporter(노드 자원).

자동화 자체 메트릭은 다음과 같다.

| 메트릭 | 의미 |
| --- | --- |
| `incidents_received_total{source}` | 접수된 장애 수(alertmanager, cloudwatch, api) |
| `incidents_completed_total{outcome}` | 결과별 완료 수(resolved, escalated, no_code_fix) |
| `incident_processing_seconds` | 접수부터 완료까지 시간(히스토그램) |
| `llm_cost_usd_total{model}` | 모델별 누적 LLM 비용 |
| `queue_pending_messages` | 처리 대기 중인 큐 메시지 수 |

#### 9-3. 알림 규칙

장애 하나가 자동화에 알림 1건으로 들어가게 만드는 것이 목표다. 그래서 새 규칙을 늘리기보다 kube-prometheus-stack 기본 규칙을 쓸 수 있는 곳은 쓰고, 자동화로 보내는 알림 이름을 정해 둔다.

| 알림 | 출처 | 조건 | 대응 시나리오 |
| --- | --- | --- | --- |
| ShopapiException | Loki ruler(새로 작성) | 1분 안에 shopapi의 `level="ERROR"` 로그가 1건 이상 | 코드 버그(S01~S09) |
| ShopapiHighErrorRate | PrometheusRule(새로 작성) | 1분간 5xx 비율 5% 초과 | 로그가 남지 않는 5xx. ShopapiException이 떠 있으면 억제 |
| ShopapiSlowPayment | PrometheusRule(새로 작성) | 결제 API 지연 p95가 5초 초과, 2분 지속 | 외부 PG 지연(S10) |
| KubePodCrashLooping | 차트 기본 규칙 | 기본 `for: 15m`을 `2m`으로 줄임 | 기동 실패 반복(S11, S14) |
| PodOOMKilled | PrometheusRule(새로 작성) | 최근 종료 사유가 OOMKilled | 메모리 초과(S15). 차트에 기본 규칙이 없음 |
| KubeNodeNotReady | 차트 기본 규칙 | 기본 `for: 15m`을 `1m`으로 줄임 | 워커 노드 장애(S16) |

infra_local_set에서 바꿀 것은 다음과 같다. 모두 `kube-prometheus-stack.values.yaml.j2` 한 파일이다.

- `customRules`로 `KubePodCrashLooping`, `KubeNodeNotReady`의 `for`를 줄인다. 기본값 15분이면 감지 시간이 규칙 대기 시간에 묻힌다.
- 자동화 수신처 라우팅을 namespace 전체가 아니라 위 표의 알림 이름으로 좁힌다. 지금은 `shop`, `aiops` namespace의 모든 알림이 자동화로 가서, 기본 규칙(`KubePodNotReady`, `KubeDeploymentReplicasMismatch`, `KubeContainerWaiting` 등)도 같은 장애에 함께 들어온다.
- `inhibit_rules`로 같은 `namespace`, `service`에서 ShopapiException이 떠 있으면 ShopapiHighErrorRate를 억제한다. 코드 버그 하나에 두 규칙이 모두 걸리기 때문이다.

LogQL 규칙은 다음과 같다. Alertmanager가 `namespace` 라벨로 라우팅하므로 `sum by`에 `namespace`를 꼭 남긴다. 빠지면 알림이 `null` 수신처로 가서 버려진다.

```
sum by (namespace, service) (count_over_time({namespace="shop"} | json | level="ERROR" [1m])) > 0
```

Alertmanager 설정은 infra_local_set에 이미 들어 있다.

- `group_by: [alertname, namespace, service]`, `group_wait: 30s`, `group_interval: 5m`, `repeat_interval: 1h`, `send_resolved: true`
- 수신처는 클러스터 내부 주소 `http://ai-api.aiops.svc:8000/api/v1/alerts/alertmanager` 하나
- 인증은 `Authorization: Bearer <토큰>` 헤더로 보낸다. 토큰은 `.ai-automation-token`에 있고 monitoring namespace의 `ai-automation-token` Secret으로 들어가 있다. FastAPI는 기존 `X-Automation-Token`과 함께 이 헤더도 받도록 고친다.
- 접속 주소는 Grafana `http://192.168.56.10:30030`, Prometheus `:30090`, Alertmanager `:30093`이다.

#### 9-4. Alertmanager 웹훅 어댑터

1. Alertmanager가 보낸 묶음에서 `status=firing`인 알림만 처리한다. `resolved`는 `alert_fingerprint`가 같은 장애 기록의 `alert_resolved_at`에 시각만 남긴다.
2. 알림 라벨에서 대상과 이름, 심각도를 꺼낸다. Shopapi 알림은 `service`를 쓴다. kube-state-metrics 기반 알림(`KubePodCrashLooping`, `PodOOMKilled`)의 `service` 라벨은 shopapi가 아니라 kube-state-metrics 자신일 수 있으므로 `namespace`, `pod`, `container`로 대상을 정한다. 3-4에 실제 알림 라벨을 확인하고 매핑을 고정한다. `KubeNodeNotReady`는 `node`를 쓴다. 요약 문구는 주석(annotations)에서 꺼낸다.
3. 코드 버그 계열 알림이면 Loki에 `startsAt` 5분 전부터 지금까지 해당 서비스의 ERROR 로그를 조회해 가장 최근 `exc_info`를 스택트레이스로 쓴다.
4. 스택트레이스가 없는 알림(지연, CrashLoop, OOMKilled, 노드 장애)은 알림 내용과 Pod 상태 요약만 넘긴다. 분류 단계에서 비코드 장애로 판단해 런북 안내로 끝나는지 확인한다. Kubernetes 장애 런북은 `knowledge/k8s/`에 둔다.
5. 기존 `IncidentIn` 형식으로 바꿔 같은 중복 제거와 큐 적재 경로를 탄다. n8n의 Slack 메시지 파싱은 3단계에서 빠지고, Slack은 결과 보고와 승인 용도로만 쓴다.

#### 9-5. Grafana 대시보드

- shopapi 상태: 요청 수, 5xx 비율, 지연 시간 p95, Pod 재시작 횟수, 최근 에러 로그 패널, 발생한 알림
- 자동화 현황: 일별 장애 수와 결과, 자동 수정 성공률, 장애당 평균 LLM 비용, 모델별 호출 비율, 큐 대기 수. 이력성 지표는 PostgreSQL을, 실시간 지표는 Prometheus를 데이터 소스로 쓴다.
- 대시보드는 JSON으로 내보내 `grafana_dashboard` 라벨을 단 ConfigMap으로 관리해 저장소에 남긴다.

#### 9-6. 3단계 추가 시나리오

| ID | 유형 | 장애 내용 | 만드는 방법 | 예상 결과 |
| --- | --- | --- | --- | --- |
| S14 | infra | 잘못된 Secret으로 shopapi가 기동 실패를 반복 | Secret 값을 잘못 바꿔 재배포 | CrashLoop 알림, 런북 안내 |
| S15 | infra | 메모리 초과로 Pod 종료 | 메모리 limit을 낮추고 큰 요청 전송 | OOMKilled 알림, 런북 안내 |
| S16 | infra | 워커 노드 장애 | `vagrant halt k3s-w1` 후 3분 뒤 `vagrant up k3s-w1` | 노드 복구 후 알림이 전달되고, 진행 중이던 장애가 체크포인트부터 이어짐 |
| S17 | infra | 노드 디스크 부족 | k3s-w1의 실험용 볼륨(`/srv/appdata`)을 임시 파일로 90% 넘게 채움 | 디스크 알림, 런북 안내와 정리 플레이북 승인 요청, 승인 후 해소 |

S16에서 k3s-w1을 멈추면 앱 쪽이 모두 함께 멈춘다. shop, aiops의 모든 Pod가 `workload=app` 노드인 k3s-w1에만 뜰 수 있고, k3s-w2는 관측 도구 전용, k3s-cp는 메모리 2GB라 옮겨 갈 곳이 없다. postgres와 redis는 local-path PVC라 데이터가 k3s-w1 디스크에 묶여 있어서, 라벨을 풀어도 다른 노드로 옮길 수 없다. 자동화 api도 멈추므로 Alertmanager의 `KubeNodeNotReady` 알림은 노드가 돌아올 때까지 받을 곳이 없다.

그래서 S16은 "다른 노드에서 재개"가 아니라 "노드 복구 후 재개"를 확인한다.

- 감지: Alertmanager UI(`:30093`)에서 `KubeNodeNotReady`가 뜬 시각을 기록한다. 자동화 수신 시각과 따로 적는다.
- 전달: 노드가 돌아온 뒤 Alertmanager가 실패한 웹훅을 다시 보내는지, 보낸다면 언제인지 기록한다. 재전송 시점은 Alertmanager 동작에 달려 있어서 미리 단정하지 않는다.
- 재개: 노드를 멈추기 전에 S02 분석을 걸어 두고, 노드 복구 후 그 장애가 체크포인트부터 이어지는지, 추가 LLM 호출과 중복 회신이 몇 번인지 기록한다.

"다른 곳에서 이어 가기"는 노드 대신 워커 Pod 단위로 확인한다. 분석 도중 처리 중인 워커 Pod를 `kubectl delete pod --force --grace-period=0`으로 지우면, 다른 워커 replica가 30초 뒤 메시지를 회수해 체크포인트부터 이어 가야 한다. 2단계의 `docker kill` 실험을 클러스터에서 다시 하는 것이고, 2단계의 PostgreSQL 체크포인터가 여기서 의미를 갖는다.

#### 9-7. 고객사 분리

MSP는 여러 고객사 환경을 한 운영 조직이 맡는다. 이를 namespace 두 개로 흉내 낸다.

- `shop` namespace 대신 `tenant-a`, `tenant-b`에 shopapi를 1개씩 올린다. 메모리를 늘리지 않도록 고객사당 replica는 1개로 둔다.
- 고객사는 namespace 이름에서 정한다. 알림 규칙과 LogQL은 `namespace=~"tenant-.*"`로 넓히고, Alertmanager 라우팅도 두 namespace를 받도록 infra_local_set에서 고친다.
- 자동화는 장애 정보에 `tenant`를 붙인다. 결과는 고객사별 Slack 채널로 보내고, 런북 검색은 `service`와 함께 `tenant`로 범위를 줄인다. 고객사 공통 런북은 `tenant: common`으로 둔다.
- 대시보드와 이력 집계도 고객사별로 나눠 본다.

#### 9-8. 승인 후 조치

자동화는 런북 안내에서 멈추지 않고, 사람이 승인하면 정해진 조치까지 실행한다. 조치 범위는 노드 OS 수준의 정리 작업으로 좁힌다.

1. 런북에 조치 이름(`cleanup-appdata`, `restart-service` 등)이 적혀 있고 분류가 비코드 장애이면, 회신에 승인 버튼을 붙인다.
2. 승인되면 자동화가 aiops namespace에 Kubernetes Job을 만든다. Job 이미지는 Ansible만 들어 있고, `deploy/ansible/`의 허용 목록 플레이북 중 하나를 대상 노드 하나에만 실행한다. 플레이북 이름과 대상은 장애 기록에서만 가져오고, LLM 출력은 쓰지 않는다.
3. SSH 키는 aiops namespace의 Secret에 두고, 자동화 서비스 계정은 aiops의 Job 생성 권한만 가진다.
4. 승인자, 승인 시각, 실행 결과, Job 로그를 장애 기록(`actions` 테이블)에 남긴다. 같은 장애에 같은 조치는 한 번만 실행한다.
5. 조치 뒤 알림이 해소되면 `alert_resolved_at`으로 조치 효과를 확인한다.

S17은 노드 루트 디스크 대신 k3s-w1에 만든 1GB 실험용 볼륨을 채운다. 루트 디스크를 채우면 kubelet이 Pod를 축출하고, 맥 디스크도 함께 늘어난다. 볼륨은 infra_local_set의 Ansible로 만들고(루프 장치로 마운트), node-exporter가 이 마운트 지점을 수집한다. 기본 디스크 규칙의 `for`가 길면 9-3처럼 줄이거나 이 마운트 지점 전용 규칙을 쓴다.

#### 9-9. 3단계 실험

LLM 성능은 1, 2단계에서 측정했으므로 여기서는 감지와 연결 구간, 클러스터 장애 대응에 집중한다. 시나리오마다 1회씩 돌린다.

| 실험 | 방법 | 측정 |
| --- | --- | --- |
| 감지 시간 | 버그 주입 후 트리거 요청, 웹훅 수신 시각 기록 | 장애 발생부터 감지까지 시간 |
| 전체 처리 시간 | 같은 실행에서 Slack 회신 시각 기록 | 장애 발생부터 회신까지 시간 |
| 알림 묶기 | 실패 요청 50회를 1분 안에 발생 | Alertmanager 알림 수 대비 생성된 장애 수. 목표는 1건 |
| 비코드 알림 분류 | S10, S11, S14, S15, S16 | 런북 안내로 끝난 비율, 고성능 모델 호출 0회 확인 |
| 워커 Pod 종료 중 재개 | 분석 도중 처리 중인 워커 Pod 강제 삭제 | 다른 replica에서 재개까지 시간, 추가 LLM 호출 수, 중복 회신 수 |
| 노드 장애 후 재개(S16) | 분석 도중 k3s-w1 중지, 3분 뒤 복구 | 알림 감지 시각, 웹훅 전달 시각, 복구 후 재개까지 시간, 추가 LLM 호출 수 |
| 해소 처리 | 장애 원복 후 알림 해소 | `alert_resolved_at`이 장애 기록에 남는지 |
| 고객사 분리 | 같은 버그를 tenant-a, tenant-b에 차례로 주입 | 각 고객사 채널로만 회신되는지, 런북 검색이 다른 고객사 문서를 가져오지 않는지 |
| 승인 후 조치(S17) | 볼륨을 채우고 회신의 승인 버튼 클릭 | 알림부터 승인 요청까지, 승인부터 해소까지 시간, 조치 기록이 남는지 |

감지 시간은 여러 대기 시간의 합이다. 결과를 적을 때 아래 값을 함께 적고, 오탐과 감지 속도 사이에서 이 값을 어떻게 정했는지 정리한다.

- Prometheus 수집 주기와 규칙 평가 주기(kube-prometheus-stack 기본 30초)
- Loki ruler 평가 주기(Loki 기본 1분)
- 규칙의 `for`
- Alertmanager `group_wait`(30초)

측정한 감지 시간이 이 합보다 크게 길면, 어느 구간에서 늘어났는지 Prometheus의 `ALERTS` 시계열과 Alertmanager 로그로 찾아서 함께 적는다.

### 10. 4단계 AWS EC2 적용

4단계의 목적은 자동화 본체를 고치지 않고 감지 수단과 알림 입구만 바꿔도 감지부터 회신까지 이어지는지 확인하는 것이다. 쿠버네티스 장애 대응은 3단계에서 보였으므로 EKS는 쓰지 않고, AWS에서는 EC2 장애와 CloudWatch 경로만 다룬다. 자동화는 로컬 K3s의 것을 그대로 쓴다.

#### 10-1. 구성

| 항목 | 로컬 K3s(3단계) | AWS EC2(4단계) |
| --- | --- | --- |
| 감지 | kube-prometheus-stack, Loki ruler | CloudWatch Agent 지표와 로그, CloudWatch 경보 |
| 알림 전달 | Alertmanager 웹훅(클러스터 내부) | 경보 상태 변경 -> EventBridge 규칙 -> SQS |
| 알림 입구 | `/api/v1/alerts/alertmanager` | 자동화의 SQS 소비자 |
| 스택트레이스 조회 | Loki | CloudWatch Logs Insights |
| 자동화 위치 | aiops namespace | 같음(로컬에서 SQS를 가져감) |
| 조치 | 승인 후 Ansible 플레이북 | 안내만 함 |

SQS를 두는 이유는 두 가지다. 자동화가 SQS를 가져가는 방식이라 공개 주소, 로드밸런서, 서명 검증이 필요 없다. 자동화가 꺼져 있던 동안의 경보도 큐에 남는다. 로컬 자동화는 SQS 읽기와 삭제, CloudWatch Logs 조회 권한만 가진 IAM 사용자로 접근한다.

#### 10-2. SQS 어댑터

1. SQS 메시지에서 EventBridge 이벤트(`CloudWatch Alarm State Change`)를 꺼내 경보 이름, 새 상태, 지표와 차원을 읽는다.
2. `ALARM`은 장애로 접수하고, `OK`는 같은 경보의 `alert_resolved_at`을 남긴다(Alertmanager의 `resolved`와 같은 처리).
3. 로그 기반 경보(ERROR 로그 지표 필터)면 경보 시각 5분 전부터 CloudWatch Logs Insights로 해당 로그 그룹의 최근 스택트레이스를 조회한다.
4. 서비스와 고객사는 경보 이름 규칙(`<tenant>-<service>-<alertname>`)에서 정한다.
5. 기존 `IncidentIn`으로 바꿔 같은 중복 제거와 큐 적재 경로를 탄다. 처리를 마친 SQS 메시지만 지워서, 자동화가 중간에 죽어도 메시지가 다시 보인다.

어댑터 테스트는 실제 EventBridge 이벤트 JSON을 저장해 두고 로컬에서 돌린다. AWS가 없어도 어댑터를 검증할 수 있다.

#### 10-3. 4단계 시나리오

| ID | 장애 내용 | 감지 | 예상 결과 |
| --- | --- | --- | --- |
| S18 | EC2의 shopapi 코드 버그(S01 패치 재사용) | ERROR 로그 지표 필터 경보 | 자동 수정, Slack 회신 |
| S19 | EC2 디스크 사용률 90% 초과 | CloudWatch Agent 디스크 지표 경보 | 코드 수정 없이 런북 안내 |

#### 10-4. 비용과 정리

- 리소스는 Terraform 하나로 만들고 지운다. 모든 리소스에 `project=ai-incident` 태그를 붙이고, `terraform destroy` 후 태그로 남은 리소스를 조회해 0개인지 확인한다.
- EC2는 퍼블릭 서브넷의 작은 인스턴스 1대로 두고 인바운드를 열지 않는다. 접속은 SSM Session Manager로 한다. NAT 게이트웨이와 로드밸런서는 만들지 않는다.
- 실행 전에 AWS 요금 계산기로 비용을 계산하고 AWS Budgets 알림을 건다.

### 11. 리스크와 대응

| 리스크 | 대응 |
| --- | --- |
| LLM 비용 초과 | 장애당 상한 유지, 첫 5회 실행 후 전체 예산 재계산 |
| LLM 결과가 매번 다름 | 시나리오당 3회 반복, 평균과 범위 보고. 범위 안의 차이는 "차이 없음"으로 적음 |
| 테스트 통과가 수정 성공이 아님 | 숨은 테스트와 전체 테스트로 판정, 거짓 통과를 별도 지표로 보고 |
| 직접 출제한 시나리오라 쉬움 | 난이도 분포, 실패 예상 시나리오(S08, S09) 포함, 실패도 그대로 보고 |
| Slack, n8n 연동에 시간 소요 | 측정은 API 직접 호출로 진행, Slack은 경로 검증만 |
| 민감 정보 유출 | 가짜 데이터만 사용, 실제 서비스 로그는 쓰지 않음 |
| LLM 생성 코드 실행 위험 | 대상 서비스가 직접 만든 저장소이고, 복사본에서만 실행 |
| 관측 도구, K3s 설정에 시간 소요 | 설치는 infra_local_set에서 끝남. 대시보드는 최소 구성부터, 감지부터 회신까지 완주를 우선 |
| VM 메모리 부족으로 Pod 축출 | Prometheus 보관 1일, Loki single binary는 이미 적용됨. n8n 미배포 |
| 같은 태그 재배포로 버그가 주입되지 않음 | 시나리오마다 새 태그(`<시나리오>-<sha>`) |
| 한 장애에 알림이 여러 건 들어옴 | 자동화 라우팅을 알림 이름으로 좁히고, `inhibit_rules`로 중복 규칙 억제 |
| 노드 장애 실험 중 자동화도 함께 멈춤 | S16은 노드 복구 후 재개로 측정, 다른 곳에서 재개는 워커 Pod 삭제로 측정. 복구 절차(`vagrant up k3s-w1`) 기록 |
| 알림이 너무 늦거나 너무 자주 옴 | `for`, `group_wait`, 평가 주기를 실험 결과와 함께 기록하고 조정 근거로 사용 |
| 메트릭 전용 알림에 스택트레이스 없음 | Loki 조회 실패 시 스택트레이스 없이 넘기고, 런북 안내로 끝나는지 확인 |
| AWS 리소스 삭제 누락으로 과금 | Terraform으로만 생성, 태그로 잔여 리소스 확인, AWS Budgets 알림 |
| 자동화 IAM 자격 증명 유출 | SQS 읽기와 삭제, Logs 조회만 허용한 전용 IAM 사용자, 4단계 종료 시 키 삭제 |
| 승인 조치가 잘못된 대상에 실행됨 | 허용 목록 플레이북, 대상 노드 1개 지정, 플레이북 이름과 대상은 LLM이 아닌 장애 기록에서만 가져옴, 같은 조치 한 번만 실행 |
| 디스크 실험 중 Pod 축출 | 루트 디스크 대신 1GB 실험용 볼륨만 채움 |
| 환경마다 자동화 코드가 갈라짐 | 환경 차이는 알림 어댑터, 로그 조회, 배포 설정에만 둔다. 어댑터 출력은 모두 `IncidentIn` |

### 12. 결과 정리와 이력서 반영

1단계 결과 표는 다음 양식으로 채운다. 값은 모두 실제 측정값만 넣는다.

| 지표 | 조건 A(최적화 켬) | 조건 B(최적화 끔) |
| --- | --- | --- |
| 분류 정확도 | | |
| 자동 수정 성공률 | | |
| 거짓 통과 | | |
| 평균 시도 횟수 | | |
| 처리 시간 중앙값 | | |
| 장애당 평균 비용 | | |
| 테스트 실행 시간 | | |
| 재발 시 LLM 호출 수 | | |

이력서 문장 틀은 다음과 같다. N에는 측정값만 넣는다. 비용 절감은 최적화 묶음 전체의 효과로 적고, 개별 최적화에 돌리지 않는다(7장).

> 로컬 K3s와 AWS EC2에서 Prometheus, Loki, Alertmanager, CloudWatch로 감지한 장애를 LangGraph로 분석해 수정안을 만들고, 영향 범위 기반 회귀 테스트로 검증한 뒤 고객사별 Slack 채널로 보고하는 자동화 파이프라인 구축. 운영 환경이 바뀌어도 알림 어댑터만 추가해 같은 자동화로 처리하고, 비코드 장애는 승인 후 Ansible 플레이북으로 조치. 장애 시나리오 19개(코드 버그 10개, 인프라와 Kubernetes, EC2 장애 7개, 재발과 중복 2개)를 대상으로 코드 버그 N종 자동 수정, 비코드 장애 N종 런북 안내, 장애 발생부터 수정안 보고까지 평균 N분. 모델 분리, 조기 종료, 수정안 재사용으로 장애당 LLM 비용 N% 절감, 선별 테스트로 검증 시간 N% 단축. PostgreSQL 체크포인터 도입으로 워커 장애 시 재처리 비용 N% 감소

3단계 결과 표는 다음 양식으로 채운다.

| 지표 | 값 | 비고 |
| --- | --- | --- |
| 감지 시간 중앙값 | | 수집 주기, 평가 주기, `for`, `group_wait` 설정값 함께 기록 |
| 장애 발생부터 Slack 회신까지 중앙값 | | |
| 알림 묶기(알림 수 대비 장애 수) | | 실패 요청 50회 기준 |
| 비코드 알림의 런북 안내 비율 | | S10, S11, S14, S15, S16 |
| 워커 Pod 종료 후 재개 시간 | | 추가 LLM 호출 수, 중복 회신 수 함께 기록 |
| 노드 장애 감지 시간, 복구 후 재개 시간 | | 웹훅 전달 시각 함께 기록 |

3단계 MSP 운영 항목은 다음 양식으로 채운다.

| 지표 | 값 | 비고 |
| --- | --- | --- |
| 고객사 간 회신 혼선 | | 다른 고객사 채널로 간 회신 수. 목표 0 |
| 알림부터 승인 요청까지 | | S17 |
| 승인부터 알림 해소까지 | | S17, 조치 기록 남음 여부 |

4단계 결과 표는 환경별로 같은 지표를 나란히 적는다. 값이 크게 다르면 어느 구간(수집, 평가, 전달, 처리)에서 차이가 났는지 함께 적는다.

| 지표 | 로컬 K3s | AWS EC2 |
| --- | --- | --- |
| 감지 시간 | | |
| 장애 발생부터 Slack 회신까지 | | |
| 감지부터 회신까지 완주 여부 | | |
| 환경 추가에 든 코드 변경 | 기준 | 어댑터 파일 수, 줄 수 |
| 실험 기간 AWS 비용 | 0 | |

### 13. 1-1 체크리스트

- [ ] `targets/shopapi` 생성(FastAPI 앱, 모듈 6개)
- [ ] 모듈별 테스트 30개 내외 작성, 전부 통과 확인
- [ ] `docker-compose.yml` 워커 볼륨을 `./targets/shopapi:/repo:ro`로 변경
- [ ] 워커 컨테이너 안에서 shopapi 테스트 실행 확인(`docker compose exec worker python -m pytest /repo -q`)
- [ ] `knowledge/shopapi/`에 런북 3~5개 작성 후 `scripts.ingest_knowledge` 실행
- [ ] `scenarios/` 구조 만들고 S01(patch, yaml, 숨은 테스트) 작성
- [ ] Anthropic 요금표 확인 후 `.env`의 `MODEL_PRICES` 갱신
- [ ] S01을 수동으로 주입하고 API로 장애 접수, 실제 LLM으로 끝까지 실행
- [ ] S01 결과(분류, 시도 횟수, 처리 시간, 비용, 숨은 테스트 결과)를 `results/phase1_first_run.md`에 기록

1-1 완료 기준은 S01 한 건이 실제 LLM으로 접수부터 결과까지 완주하고, 그 결과가 기록되는 것이다.
