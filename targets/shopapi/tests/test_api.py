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


def test_slack_alert_fits_one_message_and_keeps_innermost_frame(settings, gateway, monkeypatch):
    import dataclasses

    import httpx

    from shopapi import main
    from shopapi.inventory import Inventory

    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: sent.append(json["text"]))

    def deep(n):
        if n == 0:
            raise RuntimeError("innermost failure")
        return deep(n - 1)

    app = main.create_app(dataclasses.replace(settings, slack_webhook_url="https://hooks.example/x"), Inventory(), gateway)
    app.add_api_route("/boom", lambda: deep(200))
    from fastapi.testclient import TestClient

    assert TestClient(app, raise_server_exceptions=False).get("/boom").status_code == 500
    [text] = sent
    assert len(text) <= main.SLACK_TEXT_LIMIT
    assert text.startswith("[shopapi] critical: RuntimeError: innermost failure on GET /boom")
    assert text.count("```") == 2 and "RuntimeError: innermost failure" in text.split("```")[1]
