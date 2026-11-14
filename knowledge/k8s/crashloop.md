---
service: common
category: general
doc_type: runbook
updated_at: 2026-11-14
---
# Pod 기동 실패 반복(KubePodCrashLooping) 대응

## 증상

`KubePodCrashLooping` 알림이 뜬다. Pod 상태가 `CrashLoopBackOff`이고 재시작 횟수가 계속 오른다. 서비스 요청은 남은 정상 Pod가 받거나, 모두 실패하면 502, 503이 난다.

## 판단

배포 직후에 시작됐다면 설정이나 Secret 문제가 먼저다. 이미지의 코드가 같은데 갑자기 시작됐다면 의존 서비스(DB, 외부 API) 접속 실패를 의심한다. 기동 중 예외는 코드 수정 대상이 아닌 경우가 많다.

## 조치

1. 직전 컨테이너의 종료 로그를 본다. `kubectl -n <namespace> logs <pod> --previous`
2. 종료 사유와 이벤트를 본다. `kubectl -n <namespace> describe pod <pod>`에서 `Last State`, `Reason`, `Exit Code`, Events를 확인한다.
3. `MissingSetting`, `KeyError`처럼 설정 이름이 나오면 Secret과 ConfigMap의 키를 확인한다. `kubectl -n <namespace> get secret <name> -o jsonpath='{.data}'`로 키 이름만 확인한다. 값은 출력하지 않는다.
4. 최근 배포 때문이면 이전 버전으로 되돌린다. `kubectl -n <namespace> rollout undo deployment/<name>`
5. 원인을 고친 Secret이나 ConfigMap을 적용한 뒤 `kubectl -n <namespace> rollout restart deployment/<name>`로 다시 띄운다.

## 확인

`kubectl -n <namespace> get pods`에서 재시작 횟수가 멈추고 Ready가 되면 해소다. 알림은 몇 분 뒤 resolved로 바뀐다.
