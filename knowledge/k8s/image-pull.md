---
service: common
category: general
doc_type: runbook
updated_at: 2026-11-16
---
# 이미지를 받지 못함(PodImagePullFailed) 대응

## 증상

`PodImagePullFailed` 알림이 뜬다. 새 Pod의 상태가 `ErrImagePull` 또는 `ImagePullBackOff`이고 컨테이너가 시작하지 않는다. 컨테이너가 뜬 적이 없으므로 애플리케이션 로그도 없다. 롤링 배포 중이면 이전 Pod가 계속 요청을 받아 사용자 오류는 보이지 않을 수 있다.

## 판단

거의 항상 직전 배포의 이미지 문제다. 태그 오타, 아직 올리지 않은 태그, 저장소 주소 오류, 레지스트리 인증 실패 중 하나다. 코드 수정 대상이 아니다.

## 조치

1. 배포된 이미지 이름과 태그를 본다. `kubectl -n <namespace> get deploy <name> -o jsonpath='{.spec.template.spec.containers[0].image}'`
2. 실패 사유를 본다. `kubectl -n <namespace> describe pod <pod>`의 Events에서 `not found`(태그 없음), `unauthorized`(인증), `no such host`(주소)를 구분한다.
3. 그 태그가 레지스트리에 있는지 확인한다.
4. 직전 리비전으로 되돌린다. `kubectl -n <namespace> rollout undo deployment/<name>`
5. 올바른 태그를 올린 뒤 다시 배포한다.

## 확인

새 Pod가 Running, Ready가 되고 `ImagePullBackOff` Pod가 사라지면 해소다. 알림은 몇 분 뒤 resolved로 바뀐다.
