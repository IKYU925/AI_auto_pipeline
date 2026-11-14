---
service: payment
category: infra
doc_type: postmortem
updated_at: 2026-06-21
---
# PG 연동 인증서 만료 포스트모템

## 요약

PG사 연동 클라이언트 인증서가 만료되어 결제 승인 요청이 401로 거절됐다. 인증서 갱신 알림을 만료 30일 전으로 당겼다.
