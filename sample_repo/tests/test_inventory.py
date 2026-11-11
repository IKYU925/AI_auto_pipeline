import pytest

from shop.inventory import Inventory


def test_add_and_remove():
    inv = Inventory()
    inv.add("A", 5)
    inv.remove("A", 2)
    assert inv.count("A") == 3


def test_remove_too_many():
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.remove("A", 1)
