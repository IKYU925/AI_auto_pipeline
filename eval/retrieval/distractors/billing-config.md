---
service: billing
category: config
doc_type: runbook
updated_at: 2026-11-01
---
# billing 기동 실패(환경변수) 대응

## 증상

billing이 뜨자마자 `MissingSetting: BILLING_DB_URL is not set`으로 종료된다. 재시작이 반복되면 배포 파이프라인의 시크릿 주입 단계를 먼저 본다.

## 조치

시크릿 저장소에 BILLING_DB_URL을 넣고 Pod를 다시 띄운다. 코드 수정 대상이 아니다.
