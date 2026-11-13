"""재고 차감."""
from dataclasses import dataclass


class InvalidQuantity(Exception):
    pass


class OutOfStock(Exception):
    pass


@dataclass(frozen=True)
class Reservation:
    sku: str
    qty: int

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"reservation quantity must be positive: {self.qty}")


class Inventory:
    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self._stock = dict(stock or {})
        self.reservations: list[Reservation] = []

    def count(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserve(self, sku: str, qty: int) -> int:
        """재고를 차감하고 남은 수량을 돌려준다. 수량이 0 이하면 InvalidQuantity(400)로 거절한다."""
        if qty <= 0:
            raise InvalidQuantity(f"quantity must be positive: {qty}")
        left = self.count(sku) - qty
        if left < 0:
            raise OutOfStock(f"not enough stock for {sku}")
        self.reservations.append(Reservation(sku, qty))
        self._stock[sku] = left
        return left
