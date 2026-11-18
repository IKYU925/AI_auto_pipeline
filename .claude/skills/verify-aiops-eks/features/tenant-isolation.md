# 고객사 분리

같은 버그가 고객사 tenant-a, tenant-b에서 나면 장애가 따로 접수되고, 회신은 각 고객사 채널(`#tenant-a-ops`, `#tenant-b-ops`)로만 간다. 근거 문서도 자기 고객사와 공통 문서만 쓴다. EKS에서는 롤백 제안도 각 고객사의 ArgoCD Application만 가리킨다.

## Sub-features

- 고객사별 회신 채널
- 고객사별 런북 검색 범위
- 고객사별 Application 롤백 제안

## How to get to it (user POV)

두 고객사 채널에 각자 카드와 패치 메시지가 하나씩 오고, 공통 채널에는 오지 않는다.

## Driving it with verify-aiops-eks

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S01 --tenants
```

증명은 다음이 모두 맞을 때다.

- `runs.csv`에 `tenant:tenant-a`, `tenant:tenant-b` 두 행이 `resolved`이고 note의 "다른 고객사 문서 0개"
- `slack_*.json`에서 `tenant-a`, `tenant-b`에 2개씩, `common`에 회신 0
- `incidents_*.json`의 `proposed_action.target`이 각각 `shopapi-tenant-a`, `shopapi-tenant-b`

지금까지 EKS 값은 105.2초, 117.5초, 합계 $0.18이다(1회).

## Gotchas

- 실행 중 사람이 카드의 롤백 버튼을 누르면 실제로 롤백된다. 측정이 끝난 고객사라면 결과에 영향은 없지만 `common` 채널에 승인 결과 메시지가 생긴다. 메시지 수를 셀 때 그 메시지는 빼고 `actions_*.json`과 맞춰 본다.
