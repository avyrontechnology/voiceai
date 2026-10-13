"""Tools error types (AGENTS.md rule 1c)."""

from __future__ import annotations

from typing import ClassVar

from voiceai.common.constants import (
    HTTP_BAD_REQUEST,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_UNPROCESSABLE_ENTITY,
)
from voiceai.common.errors import AppError, ErrorCode

__all__ = [
    "BindingRequiredError",
    "InvalidToolError",
    "PolicyConflictError",
    "SuspendedError",
    "ToolDeniedError",
    "ToolError",
    "ToolExecutionError",
    "ToolForbiddenError",
    "ToolNotFoundError",
    "VaultError",
]


class ToolError(AppError):
    """Base of the tools-module hierarchy."""


class ToolNotFoundError(ToolError):
    """A tool id named no visible row (spec 0029).

    Tenant-scoped reads make foreign rows read as missing — no oracle.
    """

    code: ClassVar[ErrorCode] = ErrorCode.NOT_FOUND
    http_status: ClassVar[int] = HTTP_NOT_FOUND


class ToolForbiddenError(ToolError):
    """A write addressed a system row (spec 0029).

    Internal tools are read-only to tenants; curation happens in code review.
    """

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    http_status: ClassVar[int] = HTTP_FORBIDDEN


class InvalidToolError(ToolError):
    """The submitted tool record violates shape or reference rules (HTTP 400)."""

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class ToolExecutionError(ToolError):
    """A tool call was refused before any upstream request (HTTP 400, spec 0056).

    Schema-invalid arguments, SSRF-blocked endpoints, and unresolvable
    credentials fail here: the message and details carry identifiers and
    schema paths only, never argument values, URLs, or exception text.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_BAD_REQUEST


class ToolDeniedError(ToolError):
    """A tool call named an id outside the agent's attached refs (HTTP 403, spec 0056).

    Out-of-scope execution is an authorization refusal, not a bad request:
    details carry the tool and agent identifiers only, never the arguments.
    """

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    http_status: ClassVar[int] = HTTP_FORBIDDEN


class BindingRequiredError(ToolError):
    """A connect omitted its binding (HTTP 422, spec 0081).

    The binding is explicit on UI and API alike (the radio pre-selects
    nothing): details name the provider only, never secrets.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_UNPROCESSABLE_ENTITY


class PolicyConflictError(ToolError):
    """No binding satisfies template and org policy at once (HTTP 422, spec 0081).

    The effective set (`allowed ∩ policy`) came back empty — e.g. a
    user_private-only template under `enforce_shared`: details carry the
    provider and policy identifiers only.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INVALID_REQUEST
    http_status: ClassVar[int] = HTTP_UNPROCESSABLE_ENTITY


class SuspendedError(ToolError):
    """A call addressed a suspended row (HTTP 403, spec 0081).

    Suspension is the reversible kill-switch (credential, template, partner):
    new calls fail closed while in-flight runs complete. Details carry the
    target identifiers only, plus the error_id stitching the log line.
    """

    code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    http_status: ClassVar[int] = HTTP_FORBIDDEN


class VaultError(ToolError):
    """A stored credential envelope cannot be opened (opaque 500, spec 0056).

    Integrity failures (tampered rows, wrong key generation, malformed
    envelopes) never echo bytes or key material: the base INTERNAL_ERROR
    code keeps the message opaque while `error_id` stitches the response
    to the log line. Canonical home (moved from `vault.py` by the
    spec-0056 integrator; `vault` re-exports the name).
    """
