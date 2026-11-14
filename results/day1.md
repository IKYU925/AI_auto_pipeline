# Day 1 결과: S01 실제 LLM 실행

실행 시각: 2026-11-13T20:26:07Z (KST 2026-11-14 05:26), 조건 A(최적화 모두 켬), 원본 행은 [day1.csv](day1.csv).

| 항목 | 값 |
| --- | --- |
| 분류 | code_bug (기대 code_bug, 맞음) |
| 결과 | resolved (기대 resolved) |
| 시도 횟수 | 1 |
| 접수부터 완료까지 | 30.3초 |
| LLM 비용 | $0.0798 (Haiku 5.5 1회, Opus 5.5 2회) |
| 토큰 | 입력 10,035, 출력 2,500 |
| 테스트 선별 | 전체 7개 테스트 파일 중 `tests/test_orders.py` 1개, 0.77초 |
| 숨은 테스트 | 통과 |
| 전체 테스트(숨은 테스트 포함) | 통과 |
| 거짓 통과 | 아님 |

AI가 만든 패치는 다음과 같다. 정답 패치(`quantity == 0`)보다 넓게 `quantity <= 0`을 막는다. 음수 수량은 재고 예약에서 따로 거절되므로 스펙과 어긋나지 않는다.

```diff
--- a/shopapi/orders.py
+++ b/shopapi/orders.py
@@ -40,4 +40,6 @@
 def average_unit_price(items: list[OrderItem]) -> float:
     """옵션 포함 개당 평균 가격. 빈 주문이면 0.0을 돌려준다(프론트엔드와 합의된 스펙)."""
     quantity = sum(i.quantity for i in items)
+    if quantity <= 0:
+        return 0.0
     return round(subtotal(items) / quantity, 2)
```

비용 검산: Opus 두 번이 토큰 대부분이다. 입력 약 9천 토큰 × $4/100만과 출력 약 2.2천 토큰 × $20/100만을 더하면 약 $0.08로 기록된 값과 맞다. Haiku 분류 1회는 1천 토큰 안팎이라 $0.001 미만이다.

같은 날 확인한 것: Haiku 5.5는 temperature 0을 400("temperature: Extra inputs are not permitted")으로 거절한다. 모든 모델에 temperature를 보내지 않는 결정(decisions/0007)이 맞았다.
