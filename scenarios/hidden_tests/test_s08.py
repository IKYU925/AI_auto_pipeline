from shopapi.inventory import Inventory, Reservation


def test_reservation_records_order_id():
    inv = Inventory({"A": 5})
    assert inv.reserve("A", 2, order_id="O-1") == 3
    assert inv.reservations == [Reservation("A", 2, "O-1")]


def test_reserve_without_order_id_still_works():
    assert Inventory({"A": 5}).reserve("A", 1) == 4


def test_reserve_api_with_order_id(client):
    r = client.post("/inventory/reserve", json={"sku": "SKU-001", "quantity": 1, "order_id": "O-1001"})
    assert r.status_code == 200
    assert r.json() == {"sku": "SKU-001", "left": 2}


def test_reserve_api_out_of_stock_is_409(client):
    assert client.post("/inventory/reserve", json={"sku": "SKU-001", "quantity": 4}).status_code == 409
