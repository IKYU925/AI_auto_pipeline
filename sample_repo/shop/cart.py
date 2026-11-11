"""장바구니."""
from dataclasses import dataclass, field

from shop.pricing import apply_discount, unit_price


@dataclass
class Item:
    name: str
    price: float
    quantity: int


@dataclass
class Cart:
    items: list[Item] = field(default_factory=list)
    discount_rate: float = 0.0

    def add(self, item: Item) -> None:
        self.items.append(item)

    def total(self) -> float:
        subtotal = sum(i.price * i.quantity for i in self.items)
        return apply_discount(subtotal, self.discount_rate)

    def average_unit_price(self) -> float:
        quantity = sum(i.quantity for i in self.items)
        return unit_price(self.total(), quantity)
