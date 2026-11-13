import pytest

from shopapi.inventory import Inventory, InvalidQuantity


@pytest.mark.parametrize("qty", [0, -1])
def test_reserve_rejects_non_positive_quantity(qty):
    inv = Inventory({"A": 5})
    with pytest.raises(InvalidQuantity):
        inv.reserve("A", qty)
    assert inv.count("A") == 5


def test_reserve_negative_quantity_is_400(client):
    r = client.post("/inventory/reserve", json={"sku": "SKU-001", "quantity": -1})
    assert r.status_code == 400
