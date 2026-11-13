import httpx
import pytest
from fastapi.testclient import TestClient

from shopapi.config import Settings
from shopapi.inventory import Inventory
from shopapi.main import create_app
from shopapi.payments import PaymentGateway


def pg_handler(request: httpx.Request) -> httpx.Response:
    body = request.read().decode()
    if '"amount": 0' in body or '"amount":0' in body:
        return httpx.Response(402, json={"reason": "zero amount"})
    return httpx.Response(200, json={"approved": True, "tid": "T-1"})


@pytest.fixture
def settings() -> Settings:
    return Settings(pg_url="http://pg", pg_api_key="k", pg_timeout_sec=1, tax_rate=0.1, page_size=5, slack_webhook_url="")


@pytest.fixture
def gateway() -> PaymentGateway:
    return PaymentGateway("http://pg", "k", 1, client=httpx.Client(base_url="http://pg", transport=httpx.MockTransport(pg_handler)))


@pytest.fixture
def client(settings, gateway) -> TestClient:
    app = create_app(settings, Inventory({"SKU-001": 3}), gateway)
    return TestClient(app, raise_server_exceptions=False)
