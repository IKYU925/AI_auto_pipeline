# AI 장애 대응 자동화

Slack 장애 알림을 받아 원인을 분석하고, 수정 코드를 만들고, 관련 회귀 테스트만 골라 검증한 뒤 결과를 Slack 스레드에 회신하는 파이프라인이다. 테스트가 실패하면 실패 로그를 붙여 수정안을 다시 만들고 다시 검증한다. 전 구간에 비용 최적화(캐시, 모델 분리, 범위 축소)를 적용했다.

## 전체 흐름

```mermaid
flowchart LR
    S[Slack 장애 알림] --> N[n8n<br/>알림 파싱]
    N -->|POST /api/v1/incidents| F[FastAPI<br/>인증, 중복 제거]
    F -->|XADD| Q[(Redis Streams<br/>메시지 큐)]
    Q -->|XREADGROUP| W[Worker]
    W --> G

    subgraph G[LangGraph]
        C[분류<br/>경량 LLM] --> R[RAG 검색<br/>메타데이터 필터 + 유사도]
        R -->|코드 수정 불필요| REP[보고서]
        R --> K{검증된 수정안<br/>있음?}
        K -->|예| D
        K -->|아니오| RC[원인 분석<br/>고성능 LLM]
        RC --> FX[수정 코드 생성<br/>고성능 LLM]
        FX --> D[Diff 분석]
        D --> I[영향 범위 분석<br/>+ 테스트 선별]
        I --> T[샌드박스 테스트 실행]
        T -->|실패, 시도 남음| FX
        T -->|실패, 시도 소진| E[담당자 에스컬레이션]
        T -->|통과| REP
        E --> REP
    end

    REP --> SL[Slack 스레드 회신]
    V[(Qdrant<br/>Vector DB)] -.-> R
```

| 단계 | 구현 위치 | 하는 일 |
| --- | --- | --- |
| 1. Slack 이벤트 | `n8n/incident_workflow.json` | `[서비스] severity: 메시지` 형식의 알림만 통과시키고, 스택트레이스를 분리해서 JSON으로 만든다 |
| 2. n8n | 같은 파일 | FastAPI로 POST. 공유 토큰을 헤더로 보낸다 |
| 3. FastAPI | `app/main.py` | 토큰 검증, 에러 지문으로 중복 알림 제거, 큐 적재 후 바로 202 응답 |
| 4. 메시지 큐 | `app/core/queue.py` | Redis Streams + Consumer Group. 워커를 늘려도 중복 처리가 없고, 죽은 워커의 메시지는 회수된다. 실패 메시지는 DLQ로 보낸다 |
| 5. LangGraph | `app/graph/` | 전체 흐름과 재시도 루프, 예산 초과 시 중단을 관리한다 |
| 6. RAG | `app/rag/` | 증분 임베딩, 메타데이터 필터 우선 검색, 임베딩 캐시, 검색 캐시 |
| 7. LLM 모델 분리 | `app/llm/router.py` | 분류는 경량 모델, 원인 분석과 수정 코드 생성은 고성능 모델 |
| 8. Diff 분석 | `app/analysis/diff_analyzer.py` | 추가, 삭제된 줄과 바뀐 함수, 클래스, 시그니처 변경을 AST로 찾는다 |
| 9. 영향 범위 | `app/analysis/impact.py` | import 그래프를 역방향으로 따라가 영향받는 모듈과 위험도를 계산한다 |
| 10. 테스트 선별, 실행 | `app/analysis/test_selector.py`, `app/testing/runner.py` | 관련 테스트만 골라 원본과 분리된 복사본에서 실행하고, 실패하면 LLM에 다시 보낸다 |

## 비용 최적화 포인트

### RAG

- 증분 임베딩: 문서마다 문서 해시와 청크 해시 목록(매니페스트)을 Redis에 저장한다. 다시 수집할 때 문서 해시가 같으면 건너뛰고, 바뀐 문서는 새로 생긴 청크만 임베딩하고 사라진 청크만 지운다. 메타데이터만 바뀌면 임베딩 없이 payload만 고친다.
- 문단 경계 청킹: 고정 길이로 자르면 한 줄 수정에도 뒤쪽 청크 경계가 전부 밀려서 모든 청크를 다시 임베딩하게 된다. 제목과 빈 줄 기준으로 잘라서 수정된 문단의 청크만 바뀌게 했다.
- 포인트 ID = uuid5(doc_id:chunk_hash): 내용이 같은 청크는 위치가 바뀌어도 같은 ID라서 다시 넣을 필요가 없다.
- 임베딩 캐시: 텍스트 해시를 키로 벡터를 Redis에 저장한다. 여러 문서에 반복되는 문단, 반복되는 검색 질의는 다시 임베딩하지 않는다.
- 메타데이터 필터 우선 검색: 서비스(+공통 문서), 카테고리, 날짜(최근 N일)로 Qdrant payload 필터를 먼저 걸고 그 안에서만 유사도 검색을 한다. 필터 결과가 0건이면 서비스 조건만 남기고 한 번 더 찾는다.
- 검색 결과 캐시: 질의에서 주문번호, 시간 같은 가변 값을 지운 뒤 캐시 키를 만든다. 문서가 바뀌면 컬렉션 버전을 올려 캐시를 자동 무효화한다.
- 기본 임베딩은 로컬 fastembed(다국어 MiniLM)라 API 비용이 들지 않는다.

### LLM

- 모델 분리: `classify`, `summarize`는 경량 모델, `root_cause`, `generate_fix`는 고성능 모델로 보낸다(`TASK_TIER`).
- 분류 캐시: 에러 지문(숫자, UUID, 시간을 지운 메시지 + 스택 프레임)을 키로 분류 결과를 캐시한다. 같은 종류의 에러는 분류 LLM을 다시 부르지 않는다.
- 조기 종료: 인프라, 설정 장애처럼 코드 수정이 필요 없으면 고성능 모델을 부르지 않고 런북 안내로 끝낸다.
- 검증된 수정안 재사용: 테스트를 통과한 수정안을 지문 기준으로 7일간 저장한다. 배포 전에 같은 에러가 재발하면 LLM 호출 없이 테스트만 다시 돌린다.
- 입력 축소: 스택트레이스 주변 코드와 의심 파일만 넣고, 문서 청크와 실패 로그는 길이 상한을 둔다. 실패 로그는 pytest의 FAILURES 섹션만 남긴다.
- 예산 상한: 장애 1건당 LLM 비용이 `INCIDENT_BUDGET_USD`를 넘으면 그래프를 멈추고 담당자에게 넘긴다. 일별 모델별 비용도 `cost:daily:YYYYMMDD`에 쌓인다.
- 중복 알림 제거: 알림이 폭주해도 같은 지문은 `DEDUPE_TTL_SEC` 동안 파이프라인을 한 번만 돈다.

### 회귀 테스트

- 전체 테스트 대신 영향 범위 안에 들어온 테스트, 패치가 직접 고친 테스트, 이름 규칙(`pricing.py` -> `test_pricing.py`)에 맞는 테스트만 실행한다.
- 직접 import하는 모듈은 바뀐 심볼 이름을 실제로 참조하는 경우만 포함해서 범위를 줄인다. 2단계 이상은 동작 변경이 전파될 수 있으므로 모두 포함한다.
- 코드 외 파일(설정, requirements), conftest 변경이 있거나 선택된 테스트가 0개면 안전하게 전체 테스트로 대체한다.
- 실패하면 실패 로그를 붙여 수정안을 다시 생성하고 다시 검증한다. `MAX_FIX_ATTEMPTS`를 넘으면 에스컬레이션한다. 패치 형식이 틀리면 테스트까지 가지 않고 바로 재생성한다.

## 실행

### 1. API 키 없이 전체 흐름 확인

Redis, Qdrant, LLM 모두 메모리 대역으로 바꿔서 한 프로세스에서 돌린다. 첫 수정안은 일부러 회귀를 일으키도록(정수 나눗셈) 만들어 두어서 재시도 루프를 볼 수 있다.

```bash
pip install -r requirements.txt
python -m scripts.demo_offline
python -m pytest
```

데모에서 확인할 수 있는 것은 다음과 같다.

- 같은 문서를 다시 수집하면 임베딩 0회
- 주문번호만 다른 중복 알림은 같은 incident로 묶임
- 첫 수정안이 `2 failed`로 실패하고, 실패 로그를 받은 두 번째 수정안이 통과
- `shop/pricing.py` 변경 시 `test_cart.py`, `test_pricing.py`만 실행되고 `test_inventory.py`는 제외
- 같은 에러 재발 시 LLM 호출 0회(분류 캐시 + 검증된 수정안 재사용)
- 결제 타임아웃 같은 인프라 장애는 경량 모델 1회만 호출

### 2. docker compose로 실제 구성 실행

```bash
cp .env.example .env      # ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, AUTOMATION_TOKEN 채우기
docker compose up -d --build
docker compose exec api python -m scripts.ingest_knowledge knowledge
```

n8n(http://localhost:5678)에서 `n8n/incident_workflow.json`을 Import한 뒤 Slack Trigger 노드에 Slack 자격 증명과 장애 채널을 지정하고 활성화한다. 노드 버전은 n8n 버전에 따라 다를 수 있으므로 Import 후 각 노드 설정을 한 번 확인한다.

Slack 알림은 다음 형식을 기준으로 파싱한다.

````
[checkout] critical: 500 on /orders/summary
```
Traceback (most recent call last):
  File "/app/shop/pricing.py", line 13, in unit_price
ZeroDivisionError: float division by zero
```
````

API를 직접 호출해서 확인할 수도 있다.

```bash
curl -X POST localhost:8000/api/v1/incidents \
  -H "X-Automation-Token: $AUTOMATION_TOKEN" -H "Content-Type: application/json" \
  -d '{"channel":"C123","service":"checkout","message":"ZeroDivisionError","stacktrace":"..."}'
curl localhost:8000/api/v1/incidents/<incident_id> -H "X-Automation-Token: $AUTOMATION_TOKEN"
```

### 3. 실제 서비스 저장소에 붙이기

- `docker-compose.yml`의 `./sample_repo:/repo:ro`를 대상 서비스 저장소로 바꾼다. 운영에서는 워커가 배포된 커밋을 clone해서 쓰는 방식이 맞다.
- `knowledge/` 아래에 런북, 포스트모템을 front matter(`service`, `category`, `doc_type`, `updated_at`)와 함께 넣고 `scripts.ingest_knowledge`를 주기적으로(문서 저장소 push 시 CI 등) 실행한다. 바뀐 청크만 임베딩되므로 자주 돌려도 비용이 거의 들지 않는다.

## 폴더 구조

```
AI_automation/
├── app/
│   ├── main.py              # FastAPI (접수, 상태 조회, 지식 문서 수집)
│   ├── worker.py            # 큐 소비 워커
│   ├── container.py         # 의존성 조립
│   ├── config.py            # 환경변수 설정
│   ├── core/                # 에러 지문, Redis Streams 큐, 상태 저장소
│   ├── rag/                 # 청킹, 임베딩 캐시, 증분 수집, 필터 검색
│   ├── llm/                 # 모델 라우터, 비용 추적, 프롬프트, 출력 스키마, 가짜 LLM
│   ├── analysis/            # 코드 컨텍스트, Diff 분석, 영향 범위, 테스트 선별
│   ├── testing/runner.py    # 샌드박스 패치 적용 + pytest 실행
│   ├── graph/               # LangGraph 상태, 노드, 워크플로
│   └── notify/slack.py      # 결과 회신
├── n8n/incident_workflow.json
├── knowledge/               # RAG 원천 문서 예시
├── sample_repo/             # 데모용 대상 저장소(버그 포함)
├── scripts/                 # 오프라인 데모, 지식 문서 수집
├── tests/                   # 파이프라인 테스트 23개
├── docker-compose.yml
└── Dockerfile
```

## 주의할 점

- 수정안은 자동으로 머지하지 않는다. 검증된 패치를 `artifacts/<incident_id>.patch`로 저장하고 Slack에 올려 사람이 승인하도록 했다. PR 자동 생성은 이 지점에 붙이면 된다.
- 테스트 실행은 임시 디렉터리 복사본에서 하지만, 이것만으로는 LLM이 만든 코드를 격리했다고 볼 수 없다. 운영에서는 테스트를 네트워크가 차단된 별도 컨테이너(`docker run --network none`)나 Job에서 실행해야 한다.
- Diff, 영향도 분석은 Python 저장소 기준이다. 다른 언어는 `ImportGraph`를 해당 언어의 의존성 분석 도구로 바꿔야 한다.
- `.env.example`의 모델 단가는 예시 값이다. 실제 요금표에 맞춰 `MODEL_PRICES`를 고쳐야 비용 추적과 예산 상한이 맞게 동작한다.
