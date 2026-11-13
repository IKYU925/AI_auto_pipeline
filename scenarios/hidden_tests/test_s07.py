import pytest

from shopapi.pricing import apply_discount, bulk_discount_rate


@pytest.mark.parametrize("qty", [50, 100])
def test_bulk_discount_rate_for_large_orders(qty):
    assert bulk_discount_rate(qty) == 0.1


def test_apply_discount_still_rejects_rate_over_one():
    with pytest.raises(ValueError):
        apply_discount(1000, 10)


def test_summary_for_large_order(client):
    r = client.post("/orders/summary", json={"items": [{"sku": "A", "price": 1000, "quantity": 50}]})
    assert r.status_code == 200
    assert r.json()["total"] == 49500.0
