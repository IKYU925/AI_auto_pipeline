---
service: common
category: infra
doc_type: runbook
updated_at: 2026-09-18
---
# 데이터베이스 커넥션 풀 고갈 대응

## 증상

요청이 커넥션을 기다리다 타임아웃에 걸린다. 풀 사용률이 100%에 붙어 있다.

## 조치

느린 쿼리를 먼저 찾고, 풀 크기는 그다음에 조정한다.
