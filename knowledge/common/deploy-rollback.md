---
service: common
category: general
doc_type: runbook
updated_at: 2026-09-30
---
# 공통 배포 롤백 절차

## 언제 롤백하는가

배포 직후 에러율이 평소의 3배 이상으로 오르면 원인 분석보다 롤백을 먼저 한다.

## 절차

ArgoCD에서 직전 리비전으로 Sync하고, Slack 장애 채널에 롤백 사실과 리비전 번호를 남긴다.
