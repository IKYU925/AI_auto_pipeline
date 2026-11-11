from app.analysis.diff_analyzer import analyze_diff, parse_unified_diff, split_patch_by_file
from app.analysis.impact import ImportGraph, analyze_impact
from app.analysis.test_selector import select_tests
from scripts.demo_responder import BAD_PATCH, GOOD_PATCH

REPO = "sample_repo"


def test_parse_counts_lines_and_status():
    files = parse_unified_diff(GOOD_PATCH)
    assert [f.path for f in files] == ["shop/pricing.py", "tests/test_pricing.py"]
    assert (files[0].added, files[0].removed) == (2, 0)
    assert files[1].added_defs == ["test_unit_price_zero_quantity"]


def test_changed_symbols_found_from_ast():
    analysis = analyze_diff(BAD_PATCH, REPO)
    pricing = analysis.files[0]
    # apply_discount는 hunk 헤더에만 등장하고 실제로 바뀌지 않았으므로 제외되어야 한다
    assert pricing.changed_symbols == ["unit_price"]
    assert pricing.signature_changed == []


def test_signature_change_detected():
    patch = """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -11,3 +11,3 @@
-def unit_price(total: float, quantity: int) -> float:
+def unit_price(total: float, quantity: int, digits: int = 2) -> float:
     \"\"\"총액을 수량으로 나눈 개당 가격.\"\"\"
     return round(total / quantity, 2)
"""
    analysis = analyze_diff(patch, REPO)
    assert analysis.files[0].signature_changed == ["unit_price"]
    impact = analyze_impact(analysis, ImportGraph(REPO))
    assert impact.risk == "high"


def test_impact_and_test_selection_skip_unrelated_module():
    graph = ImportGraph(REPO)
    impact = analyze_impact(analyze_diff(BAD_PATCH, REPO), graph)
    assert impact.changed_modules == ["shop.pricing"]
    assert impact.impacted["shop.cart"] == 1
    assert "shop.checkout" in impact.impacted
    assert "shop.inventory" not in impact.impacted

    selection = select_tests(impact, graph)
    assert not selection.full_suite
    assert selection.tests == ["tests/test_cart.py", "tests/test_pricing.py"]


def test_symbol_level_narrowing_excludes_importer_not_using_symbol():
    # apply_discount만 바뀌면 cart는 apply_discount를 쓰므로 포함, unit_price만 쓰는 모듈이 있다면 제외되는 구조
    patch = """--- a/shop/inventory.py
+++ b/shop/inventory.py
@@ -12,4 +12,4 @@
     def remove(self, sku: str, qty: int) -> None:
         if self._stock.get(sku, 0) < qty:
-            raise ValueError(f"not enough stock for {sku}")
+            raise ValueError(f"insufficient stock for {sku}")
         self._stock[sku] -= qty
"""
    graph = ImportGraph(REPO)
    impact = analyze_impact(analyze_diff(patch, REPO), graph)
    selection = select_tests(impact, graph)
    assert selection.tests == ["tests/test_inventory.py"]


def test_non_python_change_falls_back_to_full_suite():
    patch = """--- a/pytest.ini
+++ b/pytest.ini
@@ -1,3 +1,4 @@
 [pytest]
 pythonpath = .
 testpaths = tests
+addopts = -x
"""
    graph = ImportGraph(REPO)
    selection = select_tests(analyze_impact(analyze_diff(patch, REPO), graph), graph)
    assert selection.full_suite


def test_split_patch_by_file():
    parts = split_patch_by_file(GOOD_PATCH)
    assert len(parts) == 2
    assert parts[0].startswith("--- a/shop/pricing.py") and "tests/test_pricing" not in parts[0]
