# EKS 감지 경로 검증 지도

검증할 때 먼저 이 색인을 읽고, 맞는 기능 파일을 절차로 쓴다. 이 지도가 검증의 기준이다. 편한 시나리오 하나만 확인한 증명은, 지도에 다른 진입점이 있으면 완료가 아니다.

## 기본 전제

- `launch.sh`가 `ready`를 냈고 `doctor.sh`가 `doctor: OK`다.
- 보호 시간 안이다. 한 시나리오에 3~6분, 내리기에 13분이 걸리니 남은 시간으로 시나리오 수를 정한다.
- 실제 LLM, 실제 Slack, AWS 과금이 있다.

## 실행 규칙

- 모든 명령은 저장소 루트에서 실행한다. 시나리오는 `drive.sh`로만 넣는다.
- 실험 중에는 두 저장소의 main에 병합하지 않는다.
- 앞 시나리오가 남긴 알림이 풀릴 때까지 `run_cluster`가 기다린다(`run_cluster.log`의 "알림 해소 대기").

## 증명과 건너뜀 보고

- 증거는 `results/verify/eks-<RUN_ID>/`의 `runs.csv` 행, `incidents_*.json`, `slack_*.json`, `argo_*.txt`다.
- 실행하지 못한 진입점은 시도한 명령과 충족되지 않은 전제를 적는다. 다른 시나리오로 확인했다고 보고하지 않는다.

## 기능 항목 형식

각 파일은 H1 제목과 사용자에게 보이는 동작 한 단락, 그리고 `Sub-features`, `How to get to it (user POV)`, `Driving it with verify-aiops-eks`, `Gotchas` 네 개의 H2로 이뤄진다.

## 기능

- [GitOps 배포](gitops-bootstrap.md)는 빈 클러스터에서 Git만으로 앱 10개가 Healthy가 되는지 다룬다(launch 자체).
- [감지부터 회신까지](alert-path.md)는 설정 오류(S14)와 코드 버그(S01)가 로컬과 같은 결과로 회신되는지 다룬다.
- [Pod 이벤트 컨텍스트](pod-events.md)는 없는 이미지 태그(S21)에서 분류가 Pod 이벤트를 근거로 쓰고 롤백이 제안되는지 다룬다.
- [고객사 분리](tenant-isolation.md)는 같은 버그를 두 고객사에 넣으면 각 채널로만 회신되고 롤백 제안도 각자의 Application을 가리키는지 다룬다.
- [승인 후 ArgoCD 롤백](argocd-rollback.md)은 배포 직후 장애(S20)에서 승인 한 번으로 직전 이미지가 배포되는지 다룬다(사람이 버튼을 눌러야 함).
