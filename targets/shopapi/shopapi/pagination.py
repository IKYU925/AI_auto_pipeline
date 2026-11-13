"""목록 조회 페이지 계산."""
from typing import TypeVar

T = TypeVar("T")


class InvalidPage(Exception):
    pass


def total_pages(total: int, size: int) -> int:
    """항목이 없어도 1페이지(빈 페이지)는 있다."""
    return max(1, -(-total // size))


def paginate(items: list[T], page: int, size: int) -> dict:
    if size < 1:
        raise InvalidPage(f"size must be positive: {size}")
    pages = total_pages(len(items), size)
    if not 1 <= page <= pages:
        raise InvalidPage(f"page {page} out of range 1..{pages}")
    start = (page - 1) * size
    return {"items": items[start : start + size], "page": page, "pages": pages, "has_next": page < pages}
