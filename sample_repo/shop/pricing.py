"""가격 계산."""


def apply_discount(price: float, rate: float) -> float:
    """rate는 0~1 사이의 할인율이다."""
    if not 0 <= rate <= 1:
        raise ValueError("rate must be between 0 and 1")
    return round(price * (1 - rate), 2)


def unit_price(total: float, quantity: int) -> float:
    """총액을 수량으로 나눈 개당 가격."""
    return round(total / quantity, 2)
