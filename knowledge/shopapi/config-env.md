---
service: shopapi
category: config
doc_type: runbook
updated_at: 2026-11-14
---
# shopapi 기동 실패(환경변수) 대응

## 증상

shopapi가 뜨자마자 `MissingSetting: PG_API_KEY is not set`으로 종료되고 재시작을 반복한다.

## 판단

배포 설정 누락이다. 코드 수정 대상이 아니다.

## 조치

1. 배포 환경의 Secret 또는 `.env`에 `PG_API_KEY`가 있는지 확인한다.
2. 최근 배포에서 Secret 이름이나 키 이름이 바뀌었는지 확인한다.
3. 값을 넣고 재배포한다. 필수 환경변수는 `PG_API_KEY` 하나이고 나머지는 기본값이 있다.
