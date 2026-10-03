"""Tool definition documents: shareable function + webhook tools (spec 0029).

System rows (`tenant_id="system"`) are the curated internal tools every tenant
reads; tenant rows are isolated CRUD. The seeder stamps the tenant; readers
never write.
"""

from __future__ import annotations

from pydantic import Field

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.database.base import BaseFields
from voiceai.modules.tools.constants import TOOL_KIND_FUNCTION, ToolKind

__all__ = ["ToolDefinition", "ToolExecutionLog"]


class ToolDefinition(BaseFields):
    """One shareable tool row.

    Attributes:
        tool_id: Natural key (`{kind}:{name}`, pinned as `id`).
        kind: `function` (tenant or internal), `webhook`, or `internal`
            (system-only behavior tools: hangup, transfer, knowledge search).
        name: Human label, unique per tenant scope.
        description: What the tool does (surfaced to the LLM + builder).
        parameters: JSON-Schema parameter contract (OpenAI function shape).
        url: Endpoint binding for `function`/`webhook` kinds (validated on
            attach, never fetched here); `None` for pure-internal behaviors.
        method: HTTP method for the binding.
        auth_ref: Secret-store reference (never a raw secret — secrets live
            in environment/secret store, rows carry the pointer).
        timeout_s: Per-call timeout budget.
        deprecated: Hidden from pickers, still resolvable (old agents read on).
        requires_tenant_key: BYOK extension point (reserved, unenforced in v1).
        tools_version: Seed revision that wrote the row (drift visibility).
    """

    tool_id: str = Field(..., min_length=1)
    kind: ToolKind = TOOL_KIND_FUNCTION
    name: str = Field(..., min_length=1)
    description: str = ""
    parameters: dict = Field(default_factory=dict)  # why: JSON-Schema contracts are free-form JSON
    url: str | None = None
    method: str = "POST"
    auth_ref: str | None = None
    timeout_s: int = 10
    params_template: dict = Field(default_factory=dict)  # why: webhook params templates are free-form JSON
    deprecated: bool = False
    requires_tenant_key: bool = False
    tools_version: int = 1
    tenant_id: str | None = SYSTEM_TENANT_ID


class ToolExecutionLog(BaseFields):
    """One tenant-scoped execution audit row (spec 0056, Phase-1).

    Written by `ToolExecutor` for every terminal outcome — success, upstream
    failure, and refusal (allowlist/schema/SSRF) alike — so operators can
    answer "what ran for whom" without ever seeing secrets: `arguments` is
    the `redact_secrets` copy taken before the call, and `result_summary`
    carries status codes and timings only, never bodies.

    Attributes:
        tool_id: Canonical executed id (MCP namespace already stripped).
        agent_id: Owning agent (the allowlist principal).
        arguments: Redacted call arguments (secret values replaced).
        result_summary: Short safe outcome line (never raw secrets or bodies).
        success: Whether the call answered successfully.
        execution_ms: Wall time of the guarded execution.
        error_id: Log-stitching id for failures (matches the returned result).
    """

    tool_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    arguments: dict = Field(default_factory=dict)  # why: redacted JSON args are free-form
    result_summary: str = ""
    success: bool = False
    execution_ms: int = Field(default=0, ge=0)
    error_id: str | None = None
