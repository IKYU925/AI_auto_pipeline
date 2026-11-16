# 고객사 분리

고객사는 namespace(tenant-a, tenant-b)로 정한다. 같은 버그가 두 고객사에 나면 장애 2건으로 따로 접수되고, 회신은 각 고객사 채널(`#tenant-a-ops`, `#tenant-b-ops`)로만 가고, 런북 검색은 자기 고객사 문서와 공통 문서만 쓴다.

## Sub-features

- 고객사 판별(namespace 라벨)과 지문 분리
- 고객사별 회신 채널(`TENANT_SLACK_CHANNELS`)
- 고객사 범위 런북 검색(`knowledge/tenants/`)

## How to get to it (user POV)

tenant-a의 shopapi(`http://tenant-a.192.168.56.10.nip.io`)에 버그가 배포된다. `#tenant-a-ops`에 카드가 올라오고 `#tenant-b-ops`, `#aiops-report`에는 아무것도 오지 않는다. 이어서 tenant-b에 같은 버그가 나면 `#tenant-b-ops`에만 따로 온다.

## Driving it with verify-aiops-cluster

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S01 --tenants
```

`--tenants`는 코드 버그 시나리오 하나를 tenant-a, tenant-b에 차례로 넣는다. `runs.csv`에 2행이 생긴다.

증명은 다음이 모두 맞을 때다.

- `incidents_*.json`에 `tenant`가 각각 `tenant-a`, `tenant-b`인 장애 2건, 둘 다 `resolved`
- `slack_*.json`에서 `tenant-a`와 `tenant-b` 채널에 각 2개, `common` 0개
- `runs.csv`의 `note` 또는 `incidents`의 `result.context_doc_ids`에 다른 고객사 문서(`tenants/tenant-b-*`가 tenant-a 장애에) 0개

비용은 약 $0.18이다.

## Gotchas

- tenant-b 장애는 첫 장애의 중복 제거 기간(10분) 안에 들어와도 새 장애로 접수되어야 한다. 하나로 묶이면 지문에 고객사가 빠진 것이다.
- 고객사 채널에 봇이 초대되어 있지 않으면 `slack_*.json`의 `error`가 `not_in_channel`이다. 회신 실패이지 분리 실패가 아니다. 보고할 때 구분한다.
