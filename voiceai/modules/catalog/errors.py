"""Catalog error types (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import HTTP_BAD_REQUEST, HTTP_NOT_FOUND
from voiceai.common.errors import AppError, ErrorCode

__all__ = ["CatalogError", "CatalogNotFoundError", "InvalidLanguageError"]


class CatalogError(AppError):
    """Base of the catalog-module hierarchy."""


class CatalogNotFoundError(CatalogError):
    """An unknown modality/provider/model was addressed (spec 0022).

    Fail-closed by design: a typo'd query answers 404 with the valid values,
    never an empty 200 that renders a broken empty dropdown.
    """

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class InvalidLanguageError(CatalogError):
    """A language code is not well-formed BCP-47 (spec 0022, slice 2 gate)."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST
