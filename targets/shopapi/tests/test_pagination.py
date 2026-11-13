import pytest

from shopapi.pagination import InvalidPage, paginate, total_pages


@pytest.mark.parametrize("total,size,pages", [(0, 5, 1), (5, 5, 1), (6, 5, 2), (12, 5, 3)])
def test_total_pages(total, size, pages):
    assert total_pages(total, size) == pages


def test_paginate_first_page():
    assert paginate(list(range(12)), 1, 5) == {"items": [0, 1, 2, 3, 4], "page": 1, "pages": 3, "has_next": True}


def test_paginate_last_partial_page():
    result = paginate(list(range(12)), 3, 5)
    assert result["items"] == [10, 11] and not result["has_next"]


def test_paginate_out_of_range():
    with pytest.raises(InvalidPage):
        paginate(list(range(12)), 4, 5)
