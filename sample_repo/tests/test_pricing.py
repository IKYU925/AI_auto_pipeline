import pytest

from shop.pricing import apply_discount, unit_price


def test_apply_discount():
    assert apply_discount(100, 0.1) == 90.0


def test_apply_discount_invalid_rate():
    with pytest.raises(ValueError):
        apply_discount(100, 1.5)


def test_unit_price_keeps_decimal():
    assert unit_price(10, 4) == 2.5
