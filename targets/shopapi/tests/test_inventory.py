import pytest

from shopapi.inventory import Inventory, OutOfStock


def test_reserve_decreases_stock():
    inv = Inventory({"A": 5})
    assert inv.reserve("A", 2) == 3
    assert inv.count("A") == 3


def test_reserve_all_stock():
    assert Inventory({"A": 2}).reserve("A", 2) == 0


def test_reserve_more_than_stock():
    with pytest.raises(OutOfStock):
        Inventory({"A": 1}).reserve("A", 2)


def test_reserve_unknown_sku():
    with pytest.raises(OutOfStock):
        Inventory().reserve("X", 1)
