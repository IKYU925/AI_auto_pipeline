import httpx
import pytest

from shopapi.payments import PaymentDeclined, PaymentGateway, PaymentTimeout


def _gateway(handler) -> PaymentGateway:
    return PaymentGateway("http://pg", "k", 1, client=httpx.Client(base_url="http://pg", transport=httpx.MockTransport(handler)))


def test_approve_returns_pg_response(gateway):
    assert gateway.approve("O-1", 1000)["approved"] is True


def test_approve_declined(gateway):
    with pytest.raises(PaymentDeclined):
        gateway.approve("O-1", 0)


def test_approve_timeout():
    def slow(request):
        raise httpx.ReadTimeout("timeout", request=request)

    with pytest.raises(PaymentTimeout):
        _gateway(slow).approve("O-1", 1000)


def test_approve_server_error():
    with pytest.raises(httpx.HTTPStatusError):
        _gateway(lambda r: httpx.Response(503)).approve("O-1", 1000)
