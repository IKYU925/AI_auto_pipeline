"""재고 차감."""


class InvalidQuantity(Exception):
    pass


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self, stock: dict[str, int] | None = None) -> None:
        self._stock = dict(stock or {})

    def count(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def reserve(self, sku: str, qty: int) -> int:
        """재고를 차감하고 남은 수량을 돌려준다."""
        if qty <= 0:
            raise InvalidQuantity(f"quantity must be positive: {qty}")
        left = self.count(sku) - qty
        if left < 0:
            raise OutOfStock(f"not enough stock for {sku}")
        self._stock[sku] = left
        return left
