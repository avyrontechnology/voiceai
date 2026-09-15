"""Shared pagination behavior (Constitution V).

Every list route uses :func:`normalize_pagination` and the :class:`Page`
model so bounds and shapes stay uniform across modules.
"""

from __future__ import annotations

from typing import Generic, List, Tuple, TypeVar

from pydantic import BaseModel, Field

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """One page of a listing.

    Attributes:
        items: The result page (possibly empty, never an error).
        total: Total matching count across all pages.
        page: 1-based page number.
        page_size: Effective page size after clamping.
    """

    items: List[T] = Field(default_factory=list)
    total: int = Field(0, ge=0)
    page: int = Field(1, ge=1)
    page_size: int = Field(DEFAULT_PAGE_SIZE)


def normalize_pagination(page: int, page_size: int) -> Tuple[int, int]:
    """Clamp raw pagination input to valid bounds.

    Pages start at 1 (lower values become 1). Non-positive sizes fall back
    to ``DEFAULT_PAGE_SIZE`` (20); sizes above ``MAX_PAGE_SIZE`` (100) are
    capped.

    Args:
        page: Requested 1-based page.
        page_size: Requested page size.

    Returns:
        The ``(page, page_size)`` pair to query with.
    """
    safe_page = page if page >= 1 else 1
    if page_size <= 0:
        safe_size = DEFAULT_PAGE_SIZE
    else:
        safe_size = min(page_size, MAX_PAGE_SIZE)
    return safe_page, safe_size
