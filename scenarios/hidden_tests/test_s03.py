from shopapi.pricing import option_surcharge


def test_unknown_option_has_no_surcharge():
    assert option_surcharge(("engraving",)) == 0.0


def test_known_and_unknown_options():
    assert option_surcharge(("gift_wrap", "express", "engraving")) == 5000.0


def test_summary_with_unknown_option(client):
    body = {"items": [{"sku": "A", "price": 1000, "quantity": 1, "options": ["engraving"]}]}
    r = client.post("/orders/summary", json=body)
    assert r.status_code == 200
    assert r.json()["subtotal"] == 1000.0
