---
service: checkout
category: code_bug
doc_type: runbook
updated_at: 2026-10-20
---
# checkout 평균 단가 계산 오류 대응

## 증상

주문 요약 API에서 ZeroDivisionError가 발생하고 500 응답이 나간다.
장바구니가 비어 있는 상태에서 결제 페이지를 새로고침하면 재현된다.

## 원인 후보

shop/pricing.py의 unit_price는 수량이 0인 경우를 처리하지 않는다.
빈 장바구니의 평균 단가는 0.0으로 내려주는 것이 프론트엔드와 합의된 스펙이다.

## 주의

unit_price는 소수점 둘째 자리까지 반올림한 실수를 돌려줘야 한다. 정수 나눗셈(//)으로 바꾸면 안 된다.
