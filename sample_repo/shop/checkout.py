"""주문 API 핸들러(장애가 발생한 지점)."""
from shop.cart import Cart


def checkout_summary(cart: Cart) -> dict:
    return {
        "total": cart.total(),
        "average_unit_price": cart.average_unit_price(),
        "items": len(cart.items),
    }
