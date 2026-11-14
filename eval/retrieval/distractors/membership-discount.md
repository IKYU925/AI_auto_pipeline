---
service: membership
category: code_bug
doc_type: runbook
updated_at: 2026-10-08
---
# membership 등급 할인 규칙

- 등급 할인: gold 10%, silver 5%. 등급이 없으면 할인하지 않는다.
- 등급 할인은 쿠폰보다 먼저 적용한다. 결과 금액은 0 아래로 내려가지 않는다.
- 잘못된 등급 값은 400으로 거절한다.
