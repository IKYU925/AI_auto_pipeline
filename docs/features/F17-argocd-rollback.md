# F17. EKS 승인 후 ArgoCD 롤백

상태: 완료
관련: plan.md 10장(P5), 4-4, F06(승인 후 조치)

## 문제

3단계 롤백(`rollout-undo`)은 Deployment의 Pod 템플릿을 직전 ReplicaSet 것으로 바꾼다. ArgoCD가 관리하는 EKS에서는 이 방식이 맞지 않는다. Git과 다른 상태가 생기고, 다음 sync 때 무엇이 이길지 정해지지 않는다. 롤백도 배포 경로(ArgoCD)를 거쳐 배포 이력에 남아야 한다.

## 목표와 비목표

- 목표: EKS에서 배포 직후 장애가 나면 카드에 `argocd-rollback @ <Application>` 승인 버튼을 붙이고, 승인되면 직전 배포의 이미지로 되돌린다.
- 목표: 롤백 대상은 고객사 shopapi Application 세 개뿐이다. 자동화 본체(`aiops`)는 대상이 아니다.
- 목표: 제안 뒤 배포가 바뀌었으면 롤백하지 않는다.
- 비목표: `argocd app rollback`. 자동 sync가 켜진 앱에서는 ArgoCD가 거부한다.
- 비목표: rollout restart. 재시작으로 풀리는 시나리오가 생길 때 더한다.

## 운영자 시나리오

1. 버그 이미지가 ArgoCD로 배포되고 1분 뒤 오류 알림이 온다.
2. 고객사 채널 카드에 분석과 함께 `argocd-rollback @ shopapi-shop` 버튼이 붙는다.
3. 승인하면 몇 초 안에 직전 이미지로 배포되고, 승인자와 결과가 `actions`에 남는다.

## 요구사항

- 제안(`app/actions.py`): 배포 상태에 Application 이름이 있으면 `argocd-rollback`, 없으면 `rollout-undo`다. 장애가 배포 뒤 30분 안이고 직전 이력이 있을 때만 제안한다.
- 실행(`app/approvals.py` `ArgoRunner`): 현재 이력 ID가 제안 때와 같은지 확인한다. 같으면 Application의 `spec.source.kustomize.images`를 직전 이력의 값으로 바꾸고, 새 이력이 Synced, Healthy가 될 때까지 기다린다.
- 권한(`deploy/k8s/overlays/eks/argocd-rbac.yaml`): approvals는 세 shopapi Application만 get, patch. api는 get만.
- shopapi를 고객사마다 Application으로 나눈다(F15). 고객사 하나의 롤백이 다른 고객사를 건드리지 않는다.

## 완료 기준

- [x] Argo 배포 이력에서 제안, `aiops` 앱은 대상이 아님, 직전 이미지로 복원, 다른 배포 뒤 거부(테스트 4개)
- [x] EKS S20 승인부터 롤백 끝까지 6.4초, 알림 해소까지 105.2초, 조치 기록 succeeded
- [x] 고객사 롤백 제안이 각자의 Application만 가리킴(S01 두 고객사), tenant-a 승인 롤백 3.4초
- [x] 제안 뒤 배포가 바뀐 카드 승인은 실행되지 않음(실측 2회)

## 설계 메모

- **이미지를 되돌리고 sync는 ArgoCD에 맡긴다.** Application의 이미지 값만 바꾸면 자동 sync가 배포하고 이력이 하나 쌓인다. ArgoCD 롤백 명령을 흉내 내지 않고 평소 배포와 같은 길을 쓴다.
- **루트 앱 selfHeal을 꺼야 한다.** 하위 Application 객체를 바꾸면 selfHeal이 켜진 루트 앱이 1초 만에 되돌린다(트러블슈팅 #29). infra_template #32에서 껐다.
- **Git이 바뀌면 Git이 이긴다.** 롤백 뒤 `main`에 새 커밋이 들어오면 루트 앱 sync가 Git 값으로 맞춘다. 롤백은 완화이고, 고치는 것은 Git이다.

## 열린 질문

- 클러스터를 내린 뒤에도 카드 버튼은 눌린다. 받을 곳이 없어 아무 반응이 없다. 실험 환경에서만 생겨 고치지 않았다.

## 변경 이력

- 2026-11-17 구현(#61), EKS 검증(2, 3차 세션).
