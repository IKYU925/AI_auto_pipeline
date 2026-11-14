---
service: notify
category: config
doc_type: runbook
updated_at: 2026-10-11
---
# notify 메일 발송 실패(환경변수) 대응

## 증상

메일 발송 워커가 `MissingSetting: SMTP_HOST is not set` 로그를 남기고 종료된다. 알림 메일이 나가지 않는다.

## 조치

SMTP_HOST, SMTP_PORT를 설정하고 워커를 재시작한다.
