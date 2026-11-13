import pytest

from shopapi.pricing import (
    Coupon,
    InvalidCoupon,
    add_tax,
    apply_coupon,
    apply_discount,
    bulk_discount_rate,
    parse_coupon,
)


def test_apply_discount():
    assert apply_discount(10000, 0.15) == 8500.0


def test_apply_discount_rejects_rate_over_one():
    with pytest.raises(ValueError):
        apply_discount(1000, 1.5)


def test_apply_coupon_rate_and_amount():
    assert apply_coupon(10000, Coupon("C", rate=0.1, amount=1000)) == 8000.0


def test_apply_coupon_never_negative():
    assert apply_coupon(500, Coupon("C", amount=1000)) == 0.0


def test_parse_coupon_rejects_invalid_rate():
    with pytest.raises(InvalidCoupon):
        parse_coupon({"code": "C", "rate": 2}, {})


def test_add_tax():
    assert add_tax(1000, 0.1) == 1100.0


def test_parse_coupon_reuses_same_code():
    cache = {}
    first = parse_coupon({"code": "C", "rate": 0.1}, cache)
    assert parse_coupon({"code": "C", "rate": 0.1}, cache) is first


@pytest.mark.parametrize("qty,rate", [(1, 0.0), (9, 0.0), (10, 0.05), (49, 0.05)])
def test_bulk_discount_rate(qty, rate):
    assert bulk_discount_rate(qty) == rate
