# 설정·인프라 알림 런북 안내

Pod 기동 실패, 메모리 초과, 외부 연동 지연처럼 코드로 고칠 수 없는 장애는 경량 모델 한 번으로 분류하고 런북을 안내하고 끝난다. 알림 문구가 한 줄뿐이라 자동화가 대상 Pod의 마지막 로그 40줄을 Loki에서 꺼내 분류 근거로 쓴다.

## Sub-features

- 필수 환경변수 누락(S11), 설정값 형식 오류(S14): KubePodCrashLooping, 기대 분류 config
- 메모리 초과(S15): PodOOMKilled, 기대 분류 infra
- 결제 대행사 응답 지연(S10): ShopapiSlowPayment, 기대 분류 infra
- 노드 장애(S16): resume-on-failure.md에서 다룬다

## How to get to it (user POV)

운영자가 shopapi 설정을 잘못 바꾸거나 메모리 limit을 낮게 잡으면 Pod가 재시작을 반복한다. 2~5분 뒤 `#aiops-report`에 원인 분류와 런북 링크가 담긴 카드가 올라온다. 코드 수정안은 없다.

## Driving it with verify-aiops-cluster

```bash
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S14
RUN_ID=$RUN_ID .claude/skills/verify-aiops-cluster/bin/drive.sh S11 S15 S10
```

`run_cluster`가 `kubectl set env`, `set resources`로 장애를 만들고, 끝나면 shopapi Deployment를 매니페스트로 통째로 교체해 되돌린다.

증명은 다음이 모두 맞을 때다.

- `runs.csv`의 `outcome`이 `no_code_fix`, `category_correct`가 `True`, `heavy_calls`가 0
- `llm_calls_*.json`에 그 장애의 호출이 `classify` 1개
- `slack_*.json`의 `common` 채널에 카드 1개

비용은 건당 $0.0003 안팎이다. S14 한 건이 가장 빠른 확인이다(약 3분).

## Gotchas

- CrashLoop 감지는 `for: 2m`에 재시작 백오프가 더해져 실행마다 166~323초로 흔들린다. 감지 시간을 비교할 때 이 폭을 적는다.
- `kubectl apply`는 `set env`로 더한 필드를 지우지 않는다. 그래서 restore()가 `kubectl replace`를 쓴다. 손으로 되돌릴 때도 apply만으로는 부족하다.
- S10은 결제 타임아웃으로 ShopapiHighErrorRate도 함께 뜬다. 별도 장애로 접수되어 런북 안내로 끝나며 오류가 아니다.
