"""The one pagination implementation (AGENTS.md rule 2).

Every list endpoint takes `PaginationParams` and returns a `Page`, so bounds are enforced in a
single place: an unbounded `page_size` is a denial-of-service knob, and `MAX_PAGE_SIZE` closes
it for every current and future endpoint at the boundary (AGENTS.md §4, "abuse").
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from voiceai.common.constants import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MIN_PAGE,
    MIN_PAGE_SIZE,
)

__all__ = ["Page", "PaginationParams", "paginate"]

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Validated page request. Construct it from query parameters, never by hand."""

    page: int = Field(default=DEFAULT_PAGE, ge=MIN_PAGE)
    page_size: int = Field(default=DEFAULT_PAGE_SIZE, ge=MIN_PAGE_SIZE, le=MAX_PAGE_SIZE)

    @property
    def skip(self) -> int:
        """Return how many items to skip.

        Returns:
            The driver-level offset for the requested page (0 for the first page).
        """
        return (self.page - MIN_PAGE) * self.page_size

    @property
    def limit(self) -> int:
        """Return how many items to fetch.

        Returns:
            The driver-level limit, already clamped to `MAX_PAGE_SIZE` by validation.
        """
        return self.page_size


class Page(BaseModel, Generic[T]):
    """One page of results plus the counters a client needs to walk the rest."""

    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        """Return the total number of pages.

        Returns:
            `ceil(total / page_size)`, or 0 when the page size is not positive — a defensive
            branch, since `PaginationParams` cannot produce one.
        """
        if self.page_size <= 0:
            return 0
        return math.ceil(self.total / self.page_size)

    @property
    def has_next(self) -> bool:
        """Report whether another page exists.

        Returns:
            `True` when the current page is not the last one.
        """
        return self.page < self.pages


def paginate(items: Sequence[T], total: int, params: PaginationParams) -> Page[T]:
    """Wrap an already-sliced result set in a `Page`.

    The caller does the slicing (a database does it far better than Python), so `items` is the
    page itself while `total` counts the whole match.

    Args:
        items: The items belonging to `params.page`.
        total: Total number of matching items across all pages.
        params: The request these items answer.

    Returns:
        The populated page.
    """
    page: Page[T] = Page(items=list(items), total=total, page=params.page, page_size=params.page_size)
    return page
