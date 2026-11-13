"""주문 합계, 평균 단가."""
from dataclasses import dataclass

from shopapi.pricing import Coupon, add_tax, apply_coupon, apply_discount, bulk_discount_rate, option_surcharge


class InvalidOrder(Exception):
    pass


@dataclass(frozen=True)
class OrderItem:
    sku: str
    price: float
    quantity: int
    options: tuple[str, ...] = ()


def parse_item(raw: dict) -> OrderItem:
    """수량은 문자열로 들어와도 정수로 바꾼다. 옵션은 없을 수 있다."""
    try:
        return OrderItem(
            sku=raw["sku"],
            price=float(raw["price"]),
            quantity=int(raw["quantity"]),
            options=tuple(raw.get("options", [])),
        )
    except (KeyError, ValueError) as e:
        raise InvalidOrder(f"invalid item {raw!r}") from e


def item_total(item: OrderItem) -> float:
    return (item.price + option_surcharge(item.options)) * item.quantity


def subtotal(items: list[OrderItem]) -> float:
    return round(sum((item_total(i) for i in items), 0.0), 2)


def average_unit_price(items: list[OrderItem]) -> float:
    """옵션 포함 개당 평균 가격. 빈 주문이면 0.0을 돌려준다(프론트엔드와 합의된 스펙)."""
    quantity = sum(i.quantity for i in items)
    if quantity == 0:
        return 0.0
    return round(subtotal(items) / quantity, 2)


def summarize(items: list[OrderItem], coupon: Coupon | None, tax_rate: float) -> dict:
    sub = subtotal(items)
    quantity = sum(i.quantity for i in items)
    discounted = apply_discount(sub, bulk_discount_rate(quantity))
    return {
        "items": len(items),
        "quantity": quantity,
        "subtotal": sub,
        "total": add_tax(apply_coupon(discounted, coupon), tax_rate),
        "average_unit_price": average_unit_price(items),
    }
