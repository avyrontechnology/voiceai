"""Pagination maths and — more importantly — its bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.common.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from voiceai.common.pagination import Page, PaginationParams, paginate


class TestPaginationParams:
    """Defaults, offsets and the bounds that make an unbounded page impossible."""

    def test_defaults_match_the_project_constants(self) -> None:
        params = PaginationParams()
        assert (params.page, params.page_size) == (1, DEFAULT_PAGE_SIZE)

    def test_skip_and_limit_drive_the_driver_query(self) -> None:
        params = PaginationParams(page=3, page_size=25)
        assert (params.skip, params.limit) == (50, 25)

    def test_first_page_skips_nothing(self) -> None:
        assert PaginationParams(page=1, page_size=10).skip == 0

    def test_page_is_one_based(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

    def test_negative_page_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page=-1)

    def test_page_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page_size=0)

    def test_page_size_is_capped(self) -> None:
        with pytest.raises(ValidationError):
            PaginationParams(page_size=MAX_PAGE_SIZE + 1)

    def test_the_cap_itself_is_allowed(self) -> None:
        assert PaginationParams(page_size=MAX_PAGE_SIZE).page_size == MAX_PAGE_SIZE


class TestPage:
    """The counters a client walks pages with."""

    @pytest.mark.parametrize(
        ("total", "page_size", "expected_pages"),
        [(0, 10, 0), (1, 10, 1), (10, 10, 1), (11, 10, 2), (25, 10, 3)],
    )
    def test_page_count_rounds_up(self, total: int, page_size: int, expected_pages: int) -> None:
        page: Page[int] = Page(items=[], total=total, page=1, page_size=page_size)
        assert page.pages == expected_pages

    def test_has_next_is_true_before_the_last_page(self) -> None:
        page: Page[int] = Page(items=[1], total=25, page=1, page_size=10)
        assert page.has_next is True

    def test_has_next_is_false_on_the_last_page(self) -> None:
        page: Page[int] = Page(items=[1], total=25, page=3, page_size=10)
        assert page.has_next is False

    def test_empty_result_has_no_next_page(self) -> None:
        page: Page[int] = Page(items=[], total=0, page=1, page_size=10)
        assert (page.pages, page.has_next) == (0, False)

    def test_non_positive_page_size_cannot_divide_by_zero(self) -> None:
        page: Page[int] = Page(items=[], total=5, page=1, page_size=0)
        assert page.pages == 0


class TestPaginate:
    """`paginate` wraps an already-sliced result set."""

    def test_wraps_items_with_the_request_counters(self) -> None:
        params = PaginationParams(page=2, page_size=2)
        page = paginate([3, 4], total=5, params=params)
        assert (page.items, page.total, page.page, page.page_size) == ([3, 4], 5, 2, 2)
        assert (page.pages, page.has_next) == (3, True)

    def test_items_are_copied_into_a_list(self) -> None:
        source = [1, 2]
        page = paginate(tuple(source), total=2, params=PaginationParams())
        assert page.items == [1, 2]
        assert isinstance(page.items, list)

    def test_models_survive_the_round_trip(self) -> None:
        page = paginate([PaginationParams()], total=1, params=PaginationParams())
        assert page.items[0].page == 1
