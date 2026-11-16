# 클러스터 감지 경로 검증 지도

검증할 때 먼저 이 색인을 읽고, 맞는 기능 파일을 절차로 쓴다. 이 지도가 검증의 기준이다. 편한 시나리오 하나만 확인한 증명은, 지도에 다른 진입점이 있으면 완료가 아니다.

## 기본 전제

- `launch.sh`로 잠금을 잡았고 `doctor.sh`가 `doctor: OK`다.
- 이 실행이 잠그지 않은 클러스터에서는 drive하지 않는다.
- 실제 LLM과 실제 Slack을 쓴다. 비용은 기능 파일마다 적었다.

## 실행 규칙

- 모든 명령은 저장소 루트에서 실행한다. 시나리오는 `drive.sh`로만 넣는다.
- `run_cluster`는 시작 전에 이전 알림이 풀리길 기다린다. 앞 시나리오가 남긴 알림 때문에 몇 분 멈춰 있을 수 있다(`run_cluster.log`의 "알림 해소 대기").
- 맥이 잠들면 VM이 멈추고 시계가 밀린다. `drive.sh`는 `caffeinate -i`로 돌리지만, 덮개를 닫으면 멈춘다.

## 증명과 건너뜀 보고

- 증거는 `results/verify/cluster-<RUN_ID>/`의 `runs.csv` 행, `incidents_*.json`, `slack_*.json`이다.
- 실행하지 못한 진입점은 시도한 명령과 충족되지 않은 전제를 적는다. 다른 시나리오로 확인했다고 보고하지 않는다.

## 기능 항목 형식

각 파일은 H1 제목과 사용자에게 보이는 동작 한 단락, 그리고 `Sub-features`, `How to get to it (user POV)`, `Driving it with verify-aiops-cluster`, `Gotchas` 네 개의 H2로 이뤄진다.

## 기능

- [코드 버그 감지와 자동 수정 회신](alert-code-bug.md)은 버그 이미지 배포부터 ShopapiException 알림, 자동 수정, 공통 채널 회신, 반복 알림 합치기까지 다룬다.
- [설정·인프라 알림 런북 안내](config-infra-alerts.md)는 CrashLoop, OOMKilled, 결제 지연 알림이 Pod 마지막 로그로 분류되고 런북 안내로 끝나는지 다룬다.
- [고객사 분리](tenant-isolation.md)는 같은 버그를 tenant-a, tenant-b에 넣었을 때 각 고객사 채널로만 회신되고 다른 고객사 문서를 쓰지 않는지 다룬다.
- [승인 후 조치](approved-actions.md)는 카드의 승인 버튼으로 노드 디스크 정리(S17)와 배포 롤백(S20)이 실행되고 알림이 해소되는지 다룬다(사람이 버튼을 눌러야 함).
- [장애 중 재개](resume-on-failure.md)는 워커 Pod 강제 삭제와 노드 정지 뒤 체크포인트에서 이어 처리하고 회신이 한 번만 가는지 다룬다.
