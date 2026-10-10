"""The result shape shared by every definition validator (Rule 2).

Both the graph validator (`modules/automation`) and the workflow validator
(which has not migrated yet) return this. It lived in `platform/graphs.py`, which
made the workflow engine import a *graph* module for a shape that has nothing to
do with graphs — a cross-family import that would have outlived the migration.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = ["ValidationResult"]


class ValidationResult(BaseModel):
    """The outcome of validating a stored definition.

    Attributes:
        valid: True when there are no errors. Warnings never invalidate — they
            are advisory and do not block a deploy.
        errors: Why the definition cannot be used. These break deploys.
        warnings: What is suspicious but permitted.
    """

    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
