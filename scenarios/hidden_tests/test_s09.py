import pytest

from shopapi.pricing import InvalidCoupon, parse_coupon


def test_invalid_coupon_is_not_cached():
    cache = {}
    with pytest.raises(InvalidCoupon):
        parse_coupon({"code": "SPRING", "rate": 1.5}, cache)
    assert "SPRING" not in cache
    assert parse_coupon({"code": "SPRING", "rate": 0.1}, cache).rate == 0.1


def test_valid_order_after_invalid_coupon_with_same_code(client):
    item = {"sku": "A", "price": 1000, "quantity": 1}
    assert client.post("/orders/summary", json={"items": [item], "coupon": {"code": "SPRING", "rate": 1.5}}).status_code == 400
    r = client.post("/orders/summary", json={"items": [item], "coupon": {"code": "SPRING", "rate": 0.1}})
    assert r.status_code == 200
    assert r.json()["total"] == 990.0
