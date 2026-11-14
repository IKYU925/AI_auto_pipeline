---
service: shipping
category: infra
doc_type: runbook
updated_at: 2026-09-12
---
# shipping 배송사 API 지연 대응

## 증상

배송 등록 요청이 30초 타임아웃에 걸리고 `ShippingTimeout`이 발생한다. 외부 배송사 API의 응답 지연이다.

## 조치

SHIP_TIMEOUT_SEC 조정과 재시도 정책으로 대응한다. 코드 수정 대상이 아니다.
