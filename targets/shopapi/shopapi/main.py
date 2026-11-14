"""shopapi FastAPI 앱. 처리하지 못한 예외는 500으로 응답하고 Slack에 장애 알림을 보낸다."""
import logging
import traceback

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from shopapi import orders, pricing
from shopapi.config import Settings, load_settings
from shopapi.inventory import Inventory, InvalidQuantity, OutOfStock
from shopapi.observability import instrument, setup_logging
from shopapi.pagination import InvalidPage, paginate
from shopapi.payments import PaymentDeclined, PaymentGateway, PaymentTimeout

log = logging.getLogger("shopapi")

# 여기 없는 예외는 전부 장애(500)로 본다
DOMAIN_ERRORS: dict[type[Exception], int] = {
    orders.InvalidOrder: 400,
    pricing.InvalidCoupon: 400,
    InvalidQuantity: 400,
    InvalidPage: 404,
    OutOfStock: 409,
    PaymentDeclined: 402,
    PaymentTimeout: 504,
}

CATALOG = [{"sku": f"SKU-{n:03d}", "name": f"상품 {n}", "price": 1000.0 * n} for n in range(1, 13)]
INITIAL_STOCK = {p["sku"]: 10 for p in CATALOG}


# Slack은 4,000자가 넘는 메시지를 둘로 나눠 올린다. 나뉘면 코드 블록이 닫히지 않아 스택트레이스를 읽을 수 없다
SLACK_TEXT_LIMIT = 3500


def slack_alert_text(request: Request, exc: Exception) -> str:
    head = f"[shopapi] critical: {type(exc).__name__}: {exc} on {request.method} {request.url.path}\n```\n"
    tb = "".join(traceback.format_exception(exc))
    room = SLACK_TEXT_LIMIT - len(head) - len("...\n```")
    if len(tb) > room:
        # 원인에 가까운 안쪽 프레임과 예외 줄은 끝에 있으므로 앞부분을 줄 단위로 버린다
        tb = "...\n" + tb[-room:].split("\n", 1)[-1]
    return f"{head}{tb}```"


def notify_slack(webhook_url: str, request: Request, exc: Exception) -> None:
    if not webhook_url:
        return
    text = slack_alert_text(request, exc)
    try:
        httpx.post(webhook_url, json={"text": text}, timeout=5)
    except httpx.HTTPError:
        log.warning("slack notify failed", exc_info=True)


def create_app(
    settings: Settings | None = None,
    inventory: Inventory | None = None,
    gateway: PaymentGateway | None = None,
) -> FastAPI:
    s = settings or load_settings()
    stock = inventory or Inventory(INITIAL_STOCK)
    pg = gateway or PaymentGateway(s.pg_url, s.pg_api_key, s.pg_timeout_sec)
    coupons: dict[str, pricing.Coupon] = {}
    app = FastAPI(title="shopapi")
    setup_logging(log)
    instrument(app)

    for exc_type, code in DOMAIN_ERRORS.items():
        app.add_exception_handler(
            exc_type, lambda _req, exc, code=code: JSONResponse({"detail": str(exc)}, status_code=code)
        )

    @app.exception_handler(Exception)
    def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc,
                  extra={"path": request.url.path})
        notify_slack(s.slack_webhook_url, request, exc)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/orders/summary")
    def order_summary(body: dict = Body(...)) -> dict:
        items = [orders.parse_item(raw) for raw in body.get("items", [])]
        return orders.summarize(items, pricing.parse_coupon(body.get("coupon"), coupons), s.tax_rate)

    @app.post("/inventory/reserve")
    def reserve(body: dict = Body(...)) -> dict:
        left = stock.reserve(body["sku"], int(body["quantity"]), order_id=body.get("order_id"))
        return {"sku": body["sku"], "left": left}

    @app.post("/payments")
    def pay(body: dict = Body(...)) -> dict:
        return pg.approve(str(body["order_id"]), float(body["amount"]))

    @app.get("/products")
    def products(page: int = 1, size: int | None = None) -> dict:
        return paginate(CATALOG, page, size or s.page_size)

    return app
