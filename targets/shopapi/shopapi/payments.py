"""외부 PG 승인 요청."""
import httpx


class PaymentTimeout(Exception):
    pass


class PaymentDeclined(Exception):
    pass


class PaymentGateway:
    def __init__(self, base_url: str, api_key: str, timeout_sec: float, client: httpx.Client | None = None):
        self.timeout = timeout_sec
        self.client = client or httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"})

    def approve(self, order_id: str, amount: float) -> dict:
        try:
            r = self.client.post("/approve", json={"order_id": order_id, "amount": amount}, timeout=self.timeout)
        except httpx.TimeoutException as e:
            raise PaymentTimeout(f"PG approve timeout after {self.timeout}s order_id={order_id}") from e
        if r.status_code == 402:
            raise PaymentDeclined(r.json().get("reason", "declined"))
        r.raise_for_status()
        return r.json()
