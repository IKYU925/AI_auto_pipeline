# 코드 버그 감지와 자동 수정 회신

shopapi에 버그가 배포되면 ERROR 로그가 Loki 규칙(ShopapiException)을 띄우고, 자동화가 스택트레이스를 Loki에서 꺼내 원인을 분석하고 수정안을 만들어 테스트로 확인한 뒤 공통 채널 `#aiops-report`에 카드와 패치를 올린다. 운영자는 Slack 카드만 보면 된다.

## Sub-features

- 버그 이미지 배포와 감지(S01 빈 주문 0으로 나누기, S02 None 속성 접근, S08 시그니처 불일치)
- 반복 알림 합치기(S13, 실패 요청 여러 번이 알림 1개, 장애 1건)
- 재발(S12, 검증된 수정안 재사용)

## How to get to it (user POV)

shopapi(`http://shop.192.168.56.10.nip.io`)에 버그가 든 이미지가 배포되고 사용자가 `POST /orders/summary`를 부른다. 500이 나고 1~2분 뒤 `#aiops-report`에 장애 카드가 올라온다.

## Driving it with verify-aiops-cluster

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S01
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S13 --burst 50
```

`run_cluster`가 `scripts/inject.sh`로 패치를 넣고 `scripts/build_push.sh s01`로 버그 이미지를 올려 shop의 shopapi에 배포한다. 끝나면 패치를 되돌리고 커밋된 이미지로 복원한다.

증명은 다음이 모두 맞을 때다.

- `runs.csv`의 `outcome`이 `resolved`, `category_correct`가 `True`, `incidents_created`가 1
- `incidents_*.json`에 `alertname: ShopapiException`, `status: resolved`, `report_ts`가 있는 행
- `slack_*.json`의 `common` 채널에 그 장애의 메시지 2개(카드, 패치), 고객사 채널에는 0개
- S13은 `note`에 자동화로 간 알림 1개, 장애 1건

비용은 S01 약 $0.08, S13 약 $0.09다.

## Gotchas

- `restore()`는 overlay를 커밋된 태그로 되돌린다. 그 태그 이미지가 레지스트리에 없으면 정리 단계가 `ImagePullBackOff`로 300초 뒤 실패한다. launch.sh가 막지만, 실행 도중 레지스트리를 비웠다면 cleanup.sh를 다시 돌린다.
- 버그 이미지는 `s01-<sha>-<diff 해시>` 태그로 올라간다. 같은 태그를 다시 쓰면 노드에 받아 둔 이미지를 써서 버그가 배포되지 않는다(plan.md 9-1).
- S08은 기대가 escalated인데 수정안이 테스트를 통과해 resolved로 끝난다(거짓 통과, F10). 실패로 세고 바꾸지 않는다.
- 워커는 첫 장애에서 임베딩 모델을 올려 메모리가 69Mi에서 약 670Mi로 오른다(limit 768Mi, results/phase4_p0_resources.md).
