import pytest

from shopapi.orders import InvalidOrder, parse_item


def test_string_quantity_is_converted():
    assert parse_item({"sku": "A", "price": "1000", "quantity": "3"}).quantity == 3


def test_non_numeric_quantity_is_invalid_order():
    with pytest.raises(InvalidOrder):
        parse_item({"sku": "A", "price": 1000, "quantity": "two"})


def test_summary_with_string_quantity(client):
    r = client.post("/orders/summary", json={"items": [{"sku": "A", "price": 1000, "quantity": "2"}]})
    assert r.status_code == 200
    assert r.json()["quantity"] == 2
