---
service: inventory
category: infra
doc_type: runbook
updated_at: 2026-10-25
---
# inventory 재고 예약 타임아웃 대응

## 증상

`POST /reservations`가 504를 돌려주고 `InventoryTimeout` 로그가 쌓인다. 창고 시스템 응답 지연이 원인인 경우가 많다.

## 조치

INVENTORY_TIMEOUT_SEC를 늘리기 전에 창고 시스템 상태 페이지를 확인한다.
