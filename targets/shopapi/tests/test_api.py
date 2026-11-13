import pytest

from shopapi.config import MissingSetting, load_settings


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_order_summary(client):
    body = {"items": [{"sku": "A", "price": 1000, "quantity": 2}], "coupon": {"code": "C", "amount": 500}}
    assert client.post("/orders/summary", json=body).json()["total"] == 1650.0


def test_order_summary_invalid_item_is_400(client):
    assert client.post("/orders/summary", json={"items": [{"sku": "A"}]}).status_code == 400


def test_payment(client):
    assert client.post("/payments", json={"order_id": "O-1", "amount": 1000}).json()["approved"] is True


def test_products_page(client):
    body = client.get("/products", params={"page": 2}).json()
    assert [p["sku"] for p in body["items"]] == ["SKU-006", "SKU-007", "SKU-008", "SKU-009", "SKU-010"]


def test_products_page_out_of_range_is_404(client):
    assert client.get("/products", params={"page": 99}).status_code == 404


def test_settings_require_pg_api_key(monkeypatch):
    monkeypatch.delenv("PG_API_KEY", raising=False)
    with pytest.raises(MissingSetting):
        load_settings()
