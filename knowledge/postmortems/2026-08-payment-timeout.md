---
service: payment
category: infra
doc_type: postmortem
updated_at: 2026-08-14
---
# 결제 PG 연동 타임아웃 포스트모템

## 요약

PG사 응답 지연으로 결제 승인 요청이 30초 타임아웃에 걸렸다. 코드 수정 없이 커넥션 풀 크기 조정과 재시도 정책 변경으로 해결했다.

## 조치

결제 서비스의 HTTP 클라이언트 타임아웃을 10초로 줄이고 지수 백오프 재시도를 적용했다.
