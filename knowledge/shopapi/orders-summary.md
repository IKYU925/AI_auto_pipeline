---
service: shopapi
category: code_bug
doc_type: runbook
updated_at: 2026-11-14
---
# shopapi 주문 요약 API 오류 대응

## 증상

`POST /orders/summary`가 500을 돌려준다. 장바구니 화면과 결제 직전 화면이 이 API를 부른다.

## 스펙

- 응답 필드는 `items`, `quantity`, `subtotal`, `total`, `average_unit_price`다.
- `average_unit_price`는 옵션 추가금을 포함한 개당 평균 가격이고 소수점 둘째 자리까지 반올림한다.
- 빈 주문(항목 0개)도 정상 요청이다. 금액 필드는 모두 0.0, `average_unit_price`도 0.0으로 내려준다. 프론트엔드와 합의된 스펙이다.
- `total`은 쿠폰을 먼저 적용하고 세금을 붙인 금액이다.

## 주의

금액은 실수로 계산한다. 정수 나눗셈(//)으로 바꾸면 평균 단가가 틀려진다.
