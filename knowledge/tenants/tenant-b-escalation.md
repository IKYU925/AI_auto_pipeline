---
service: shopapi
tenant: tenant-b
category: general
doc_type: runbook
updated_at: 2026-11-15
---
# 고객사 B shopapi 장애 연락과 배포 절차

고객사 B 전용 문서다. 다른 고객사 장애에는 적용하지 않는다.

## 연락

- 주문, 결제 API(`/orders/summary`, `/payments`) 장애는 고객사 B 티켓 시스템에 긴급 티켓으로 올린다. 전화 연락은 하지 않는다.
- 고객사 B는 24시간 수정 배포를 허용한다. 검증된 수정안이면 바로 배포 요청을 올린다.

## 배포

- 수정안은 고객사 B의 배포 파이프라인에 변경 요청(CR)으로 올린다.
