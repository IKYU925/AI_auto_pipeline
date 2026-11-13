import pytest

from shopapi.orders import InvalidOrder, OrderItem, average_unit_price, parse_item, subtotal, summarize
from shopapi.pricing import Coupon


def test_parse_item_without_options():
    assert parse_item({"sku": "A", "price": 1000, "quantity": 1}).options == ()


def test_parse_item_rejects_missing_price():
    with pytest.raises(InvalidOrder):
        parse_item({"sku": "A", "quantity": 1})


def test_subtotal_includes_option_surcharge():
    items = [OrderItem("A", 1000, 2, ("gift_wrap",)), OrderItem("B", 500, 1)]
    assert subtotal(items) == 6500.0


def test_average_unit_price():
    items = [OrderItem("A", 1000, 1), OrderItem("B", 2000, 3)]
    assert average_unit_price(items) == 1750.0


def test_average_unit_price_rounds_to_two_decimals():
    assert average_unit_price([OrderItem("A", 1000, 3)]) == 1000.0
    assert average_unit_price([OrderItem("A", 100, 1), OrderItem("B", 0.01, 2)]) == 33.34


def test_summarize_applies_coupon_then_tax():
    result = summarize([OrderItem("A", 10000, 1)], Coupon("C", rate=0.1), tax_rate=0.1)
    assert result == {"items": 1, "quantity": 1, "subtotal": 10000.0, "total": 9900.0, "average_unit_price": 10000.0}
