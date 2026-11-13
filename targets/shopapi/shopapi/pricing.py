"""할인, 쿠폰, 옵션 추가금, 세금."""
from dataclasses import dataclass

OPTION_SURCHARGE = {"gift_wrap": 2000.0, "express": 3000.0}


class InvalidCoupon(Exception):
    pass


@dataclass(frozen=True)
class Coupon:
    code: str
    rate: float = 0.0
    amount: float = 0.0


def parse_coupon(raw: dict | None) -> Coupon | None:
    if raw is None:
        return None
    coupon = Coupon(code=raw["code"], rate=float(raw.get("rate", 0)), amount=float(raw.get("amount", 0)))
    if not 0 <= coupon.rate <= 1 or coupon.amount < 0:
        raise InvalidCoupon(f"invalid coupon {coupon.code}")
    return coupon


def apply_discount(price: float, rate: float) -> float:
    """rate는 0~1 사이의 할인율이다."""
    if not 0 <= rate <= 1:
        raise ValueError("rate must be between 0 and 1")
    return round(price * (1 - rate), 2)


def apply_coupon(amount: float, coupon: Coupon | None) -> float:
    """쿠폰이 없으면 금액을 그대로 돌려준다. 할인 후 금액은 0 아래로 내려가지 않는다."""
    if coupon is None:
        return amount
    return max(round(apply_discount(amount, coupon.rate) - coupon.amount, 2), 0.0)


def option_surcharge(options: tuple[str, ...]) -> float:
    """알 수 없는 옵션은 추가금 없이 무시한다."""
    return sum(OPTION_SURCHARGE.get(o, 0.0) for o in options)


def add_tax(amount: float, rate: float) -> float:
    return round(amount * (1 + rate), 2)
