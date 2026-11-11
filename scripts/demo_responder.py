"""오프라인 데모, 테스트용 가짜 LLM 응답.

첫 번째 수정안은 일부러 회귀를 일으키는 패치(정수 나눗셈)를 내고,
실패 로그를 받은 두 번째 시도에서 올바른 패치를 내도록 만들어 재시도 루프를 보여준다.
"""
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.llm.schemas import Classification, FixProposal, RootCause

BAD_PATCH = """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -11,3 +11,5 @@ def apply_discount(price: float, rate: float) -> float:
 def unit_price(total: float, quantity: int) -> float:
     \"\"\"총액을 수량으로 나눈 개당 가격.\"\"\"
-    return round(total / quantity, 2)
+    if quantity <= 0:
+        return 0.0
+    return round(total // quantity, 2)
"""

GOOD_PATCH = """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -11,3 +11,5 @@ def apply_discount(price: float, rate: float) -> float:
 def unit_price(total: float, quantity: int) -> float:
     \"\"\"총액을 수량으로 나눈 개당 가격.\"\"\"
+    if quantity <= 0:
+        return 0.0
     return round(total / quantity, 2)
--- a/tests/test_pricing.py
+++ b/tests/test_pricing.py
@@ -14,3 +14,7 @@ def test_apply_discount_invalid_rate():

 def test_unit_price_keeps_decimal():
     assert unit_price(10, 4) == 2.5
+
+
+def test_unit_price_zero_quantity():
+    assert unit_price(10, 0) == 0.0
"""


def demo_responder(schema: type[BaseModel], messages: list[BaseMessage]) -> BaseModel:
    user = str(messages[-1].content)
    if schema is Classification:
        if "timeout" in user.lower():
            return Classification(
                category="infra", needs_code_fix=False, confidence=0.85,
                summary="외부 PG 응답 지연으로 인한 타임아웃", keywords=["timeout", "PG", "재시도"],
            )
        return Classification(
            category="code_bug", needs_code_fix=True, confidence=0.92,
            summary="주문 요약 API에서 수량 0으로 나누기 발생", keywords=["ZeroDivisionError", "unit_price", "평균 단가"],
        )
    if schema is RootCause:
        return RootCause(
            root_cause="빈 장바구니에서 average_unit_price가 수량 0으로 unit_price를 호출해 ZeroDivisionError가 난다",
            suspected_files=["shop/pricing.py", "tests/test_pricing.py"],
            evidence=["shop/pricing.py:13 total / quantity", "runbook: 빈 장바구니 평균 단가는 0.0"],
            fix_strategy="unit_price에서 quantity <= 0이면 0.0을 반환한다",
        )
    if schema is FixProposal:
        if "이전 시도 1" not in user:
            return FixProposal(patch=BAD_PATCH, explanation="수량 0 가드 추가 및 나눗셈 정리", risk="low")
        return FixProposal(
            patch=GOOD_PATCH,
            explanation="수량 0 가드만 추가하고 기존 실수 나눗셈은 유지, 회귀 테스트 추가",
            risk="low",
        )
    raise ValueError(f"unexpected schema {schema}")
