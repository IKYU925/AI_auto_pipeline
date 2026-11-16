# 승인 후 조치

허용 목록에 있는 조치는 카드에 승인 버튼으로 붙는다. 사람이 버튼을 한 번 누르면 approvals 서비스(Slack Socket Mode)가 클릭을 받아 노드 플레이북 Job이나 Deployment 롤백을 실행하고, 승인자와 결과를 `actions`에 남긴다. 알림이 풀리면 해소가 기록된다.

## Sub-features

- 노드 디스크 정리(S17, AppdataDiskFull, `cleanup-appdata @ k3s-w1`, Ansible Job)
- 배포 직후 장애 롤백(S20, `rollout-undo @ shop/shopapi`, 제안 때 고정한 리비전으로)
- 승인자 제한(`ACTION_APPROVERS`, 비우면 누구나)

## How to get to it (user POV)

`#aiops-report` 카드 아래에 "승인" 버튼이 보인다. 운영자가 누르면 카드에 실행 결과가 붙고 1~2분 안에 알림이 풀린다.

## Driving it with verify-aiops-cluster

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S17
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S20
```

`run_cluster.log`에 "Slack에서 승인하세요(900초 대기)"가 나오면 사람이 Slack 카드의 버튼을 누른다. 에이전트가 버튼을 대신 누를 방법은 없다. 운영자가 자리에 없으면 이 기능은 건너뜀으로 보고한다.

증명은 다음이 모두 맞을 때다.

- `actions_*.json`에 `status: succeeded`, `approver`가 있는 행
- `runs.csv`의 `note`에 "승인부터 알림 해소까지 N초"(양수). "승인 전에 해소됨"이면 조치 효과가 아니므로 실패로 본다
- S20은 shop의 shopapi 리비전이 롤백되고, cleanup 뒤 이미지가 커밋된 태그로 돌아옴

비용은 S17 $0.0003, S20 약 $0.10이다.

## Gotchas

- S17은 k3s-w1의 실험용 볼륨 `/srv/appdata`가 있어야 한다(infra_local_set PR #7). 없으면 디스크 채우기가 루트 디스크를 채운다. drive 전에 `vagrant ssh k3s-w1 -c 'mountpoint /srv/appdata'`로 확인한다.
- 승인 버튼은 Socket Mode로 받는다. Socket Mode가 켜져 있으면 1단계의 Slack 이벤트(n8n) 경로는 이벤트를 받지 못한다.
- S20은 롤백 전까지 2초마다 버그 요청을 보낸다. 승인이 늦어지면 실패 요청이 계속 쌓인다(최대 1500초).
