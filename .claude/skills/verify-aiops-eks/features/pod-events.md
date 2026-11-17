# Pod 이벤트 컨텍스트

컨테이너가 뜨지 않는 장애는 로그도 스택트레이스도 없다. 알림을 받으면 자동화가 그 Pod의 대기 사유와 K8s 이벤트를 읽어 분류와 원인 분석에 넣는다. 배포 직후면 ArgoCD 배포 이력에서 롤백도 제안한다.

## Sub-features

- 없는 이미지 태그 배포(S21, PodImagePullFailed, ImagePullBackOff)
- 분류 근거에 이벤트의 이미지 태그와 NotFound
- ArgoCD 이력 기반 롤백 제안(`argocd-rollback @ shopapi-shop`)

## How to get to it (user POV)

`#aiops-report` 카드의 분류 줄이 "존재하지 않는 이미지 태그(...)를 참조해 NotFound"처럼 원인을 짚고, 런북 `k8s/image-pull.md`를 안내한다.

## Driving it with verify-aiops-eks

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S21
```

증명은 다음이 모두 맞을 때다.

- `runs.csv`에 `no_code_fix`, `category_correct`, `runbook_hit`가 True
- `incidents_*.json`의 `report`에 `s21-missing`과 NotFound가 있음. 알림 문구에는 둘 다 없으므로 Pod 이벤트에서 온 것이다
- `proposed_action`이 `argocd-rollback`, `shopapi-shop`
- `argo_shopapi-shop_*.txt`에 `s21-missing` 배포 이력

지금까지 EKS 값은 126.8초, $0.0003이다(1회).

## Gotchas

- 런북 안내 경로라 원인 분석 단계는 돌지 않는다. 이벤트가 원인 분석 프롬프트에 들어가는지는 이 시나리오로 확인되지 않는다.
- 이 시나리오는 승인을 기다리지 않는다. 실행이 끝나면 복원 배포가 이력을 바꾸므로, 그 뒤 카드의 롤백 버튼은 리비전 확인에 걸려 실행되지 않는다(정상).
