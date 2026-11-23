# GitOps 배포

Terraform이 ArgoCD와 루트 Application 하나만 만들면, 루트 앱이 이 저장소의 `deploy/argocd`(Helm 차트)를 읽어 앱 10개를 sync wave 순서로 만든다. 운영자는 손으로 바꾸는 값 없이 기다리기만 한다.

## Sub-features

- sync wave 순서(External Secrets -2, eks-cluster -1, 관측 0, aiops 1, 고객사 shopapi 2)
- 이미지 태그는 `deploy/argocd/values.yaml`의 `imageTag` 하나
- Secrets Manager에서 ExternalSecret으로 비밀 받기
- 모든 앱 자동 sync, selfHeal 꺼짐(루트 앱 포함)

## How to get to it (user POV)

`make eks-up` 뒤 몇 분 지나면 `kubectl -n argocd get applications`에서 10개가 모두 Synced/Healthy다. ArgoCD UI(`kubectl -n argocd port-forward svc/argocd-server 8080:443`)에서도 같다.

## Driving it with verify-aiops-eks

`launch.sh`가 이 기능을 그대로 거친다. 증명은 `timings.txt`의 `argocd_healthy_sec`(지금까지 241~334초)와 `doctor_launch.txt`의 다음 항목이다.

- `ArgoCD 앱 10개 Synced/Healthy`, `루트 앱 selfHeal 꺼짐`
- `Deployment 이미지 확인 완료(기준 <imageTag>)`에 FAIL 없음
- `ArgoCD 추적 표시 shopapi-shop:apps/Deployment:shop/shopapi`

## Gotchas

- ECR에 `imageTag`가 없으면 aiops와 shopapi가 ImagePullBackOff로 Healthy가 되지 않는다. launch가 미리 막는다.
- 루트 앱 selfHeal이 켜져 있으면(infra_template #32 이전 Terraform) 장애 주입과 롤백이 1초 만에 되돌려진다.
- Grafana 데이터 소스(uid `aiops-postgres`)는 api Pod의 `grafana-db` init 컨테이너가 만든다. launch가 따로 하지 않는다.
