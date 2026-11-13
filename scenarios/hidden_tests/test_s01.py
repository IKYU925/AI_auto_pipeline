from shopapi.orders import average_unit_price, summarize


def test_empty_order_average_unit_price_is_zero():
    assert average_unit_price([]) == 0.0


def test_empty_order_summary(client):
    r = client.post("/orders/summary", json={"items": []})
    assert r.status_code == 200
    assert r.json() == {"items": 0, "quantity": 0, "subtotal": 0.0, "total": 0.0, "average_unit_price": 0.0}


def test_empty_order_summarize_with_coupon():
    assert summarize([], None, tax_rate=0.1)["average_unit_price"] == 0.0
