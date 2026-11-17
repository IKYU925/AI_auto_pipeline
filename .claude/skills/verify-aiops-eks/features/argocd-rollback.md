# 승인 후 ArgoCD 롤백

배포 직후 장애가 나면 카드에 `argocd-rollback @ <Application>` 승인 버튼이 붙는다. 사람이 누르면 approvals 서비스가 Application 이미지를 직전 배포 이력의 값으로 되돌리고, ArgoCD 자동 sync가 배포한다. 제안 뒤 배포가 바뀌었으면 롤백하지 않는다.

## Sub-features

- 배포 직후 장애 롤백(S20)
- 리비전 확인(제안 이력과 지금 이력이 다르면 거부)
- 자동화 본체(`aiops`) 앱은 대상이 아님(권한으로도 막음)

## How to get to it (user POV)

`#aiops-report` 카드 아래 "승인" 버튼. 누르면 몇 초 안에 실행 결과가 붙고 1~2분 안에 알림이 풀린다.

## Driving it with verify-aiops-eks

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S20
```

`run_cluster.log`에 "Slack에서 승인하세요(900초 대기)"가 나오면 사람이 카드의 버튼을 누른다. 에이전트가 대신 누를 방법은 없다. 운영자가 자리에 없으면 건너뜀으로 보고한다.

증명은 다음이 모두 맞을 때다.

- `actions_*.json`에 `playbook: argocd-rollback`, `status: succeeded`, `approver`가 있고 `log`가 "이력 N -> N+1"
- `runs.csv` note의 "승인부터 조치 끝까지", "승인부터 알림 해소까지"가 양수. "승인 전에 해소됨"이면 조치 효과가 아니다
- `argo_shopapi-shop_*.txt`에 버그 이미지 다음 이력이 직전 기본 이미지

지금까지 EKS 값은 승인부터 롤백 끝까지 6.4초, 알림 해소까지 105.2초다(1회). 비용 약 $0.08.

## Gotchas

- 실험 중 main이 바뀌면 루트 앱이 Git 값으로 다시 sync해 장애가 승인 전에 풀리고, 승인한 롤백은 리비전 확인에 걸린다. doctor가 main 변경을 FAIL로 잡는다.
- 클러스터를 내린 뒤 누른 버튼은 받을 곳이 없어 아무 반응이 없다.
