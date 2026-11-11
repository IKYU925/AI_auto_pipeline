"""재고 관리(이번 장애와 무관한 모듈)."""


class Inventory:
    def __init__(self) -> None:
        self._stock: dict[str, int] = {}

    def add(self, sku: str, qty: int) -> None:
        self._stock[sku] = self._stock.get(sku, 0) + qty

    def remove(self, sku: str, qty: int) -> None:
        if self._stock.get(sku, 0) < qty:
            raise ValueError(f"not enough stock for {sku}")
        self._stock[sku] -= qty

    def count(self, sku: str) -> int:
        return self._stock.get(sku, 0)
