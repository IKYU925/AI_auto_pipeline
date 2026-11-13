from shopapi.pagination import paginate


def test_last_partial_page():
    result = paginate(list(range(12)), 3, 5)
    assert result["items"] == [10, 11] and not result["has_next"]


def test_empty_list_first_page():
    assert paginate([], 1, 5)["items"] == []


def test_products_last_page(client):
    r = client.get("/products", params={"page": 3})
    assert r.status_code == 200
    assert [p["sku"] for p in r.json()["items"]] == ["SKU-011", "SKU-012"]
