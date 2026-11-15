---
service: shopapi
tenant: tenant-a
category: general
doc_type: runbook
updated_at: 2026-11-15
---
# 고객사 A shopapi 장애 연락과 배포 절차

고객사 A 전용 문서다. 다른 고객사 장애에는 적용하지 않는다.

## 연락

- 주문, 결제 API(`/orders/summary`, `/payments`) 장애는 고객사 A 운영 담당자에게 15분 안에 알린다.
- 고객사 A는 업무 시간(평일 09~18시) 밖의 수정 배포를 승인하지 않는다. 업무 시간 밖이면 수정안만 회신에 남긴다.

## 배포

- 수정안은 고객사 A 저장소의 `hotfix/` 브랜치로 올리고, 고객사 A 담당자가 병합한다.
