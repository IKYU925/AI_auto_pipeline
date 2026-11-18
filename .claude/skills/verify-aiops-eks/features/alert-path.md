# 감지부터 회신까지

shopapi에 장애를 넣으면 Prometheus나 Loki 규칙이 알림을 띄우고, Alertmanager 웹훅으로 자동화가 받아 분석하고, Slack 공통 채널(`#aiops-report`)에 카드가 올라온다. 설정 오류는 런북 안내로, 코드 버그는 수정안과 함께 끝난다.

## Sub-features

- 설정 오류(S14, 기동 중 설정값 형식 오류, CrashLoop, 런북 안내)
- 코드 버그(S01, ArgoCD로 버그 이미지 배포, ShopapiException, 자동 수정)

## How to get to it (user POV)

장애가 나고 2~3분 뒤 `#aiops-report`에 카드가 뜬다. 코드 버그는 패치 메시지가 하나 더 붙는다.

## Driving it with verify-aiops-eks

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S14
RUN_ID=$RUN_ID .claude/skills/verify-aiops-eks/bin/drive.sh S01
```

증명은 다음이 모두 맞을 때다.

- `runs.csv`에 S14 `no_code_fix`, S01 `resolved`이고 `outcome_correct`, `category_correct`가 True
- `slack_*.json`의 `common`에 카드(S01은 패치 메시지까지)
- S01은 `argo_shopapi-shop_*.txt`에 버그 이미지 배포와 복원 배포가 이력으로 남음

지금까지 EKS 값은 S14 186.9초, S01 105.2~117.5초다(1회씩). 비용은 S14 $0.0003, S01 약 $0.08.

## Gotchas

- S01은 버그 이미지를 빌드해 ECR에 올린다(arm64, 1~2분, Docker 필요).
- 버그 이미지 배포로 Pod가 바뀌면 port-forward가 끊긴다. 루프가 다시 붙고 실행기가 연결 오류를 다시 보낸다(`RemoteProtocolError` 한두 줄은 정상).
