---
service: auth
category: code_bug
doc_type: runbook
updated_at: 2026-09-05
---
# auth 세션 만료 KeyError 대응

## 증상

세션이 만료된 사용자가 요청하면 `KeyError: 'user_id'`로 500이 난다.

## 조치

세션에 user_id가 없으면 401로 돌려준다.
