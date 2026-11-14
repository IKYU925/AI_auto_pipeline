---
service: search
category: code_bug
doc_type: postmortem
updated_at: 2026-07-19
---
# search 점수 정규화 ZeroDivisionError 포스트모템

## 요약

검색 결과가 0건일 때 관련도 점수를 정규화하면서 `ZeroDivisionError: float division by zero`가 발생해 `GET /search`가 500을 돌려줬다. 결과가 없으면 정규화를 건너뛰도록 고쳤다.
