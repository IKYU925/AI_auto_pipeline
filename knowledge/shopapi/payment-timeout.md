---
service: shopapi
category: infra
doc_type: runbook
updated_at: 2026-11-14
---
# shopapi 결제(PG) 지연, 타임아웃 대응

## 증상

`POST /payments`가 504를 돌려주거나 `PaymentTimeout`, `PG approve timeout` 로그가 쌓인다.

## 판단

코드 문제가 아니라 외부 PG 응답 지연이다. 코드 수정 대상이 아니다.

## 조치

1. PG사 상태 페이지와 공지에서 장애 여부를 확인한다.
2. 같은 시간대 다른 가맹점 결제 성공률을 확인한다.
3. 지연이 10분 넘게 이어지면 결제 수단 안내 배너를 켜고 PG사에 문의한다.
4. `PG_TIMEOUT_SEC`를 늘리는 것은 임시 조치다. 기본값 3초로 되돌리는 것을 잊지 않는다.
