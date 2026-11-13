from shopapi.orders import OrderItem, summarize
from shopapi.pricing import apply_coupon


def test_apply_coupon_without_coupon_returns_amount():
    assert apply_coupon(1234.5, None) == 1234.5


def test_summary_without_coupon(client):
    r = client.post("/orders/summary", json={"items": [{"sku": "A", "price": 1000, "quantity": 2}]})
    assert r.status_code == 200
    assert r.json()["total"] == 2200.0


def test_summarize_without_coupon():
    assert summarize([OrderItem("A", 1000, 2)], None, tax_rate=0)["total"] == 2000.0
