from shop.cart import Cart, Item


def test_cart_total_with_discount():
    cart = Cart(discount_rate=0.5)
    cart.add(Item("pen", 2.0, 3))
    assert cart.total() == 3.0


def test_average_unit_price():
    cart = Cart()
    cart.add(Item("pen", 2.0, 1))
    cart.add(Item("book", 5.0, 1))
    assert cart.average_unit_price() == 3.5
