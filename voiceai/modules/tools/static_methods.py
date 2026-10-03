"""Pure tool helpers: natural keys, schema checks, fingerprints (specs 0029, 0056).

No I/O, no imports beyond stdlib + constants — deterministic and unit-tested.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.modules.tools.constants import MCP_NAMESPACE_SEPARATOR, MCP_PREFIX, TOOL_ID_SEPARATOR

__all__ = [
    "build_tool_id",
    "fingerprint_tool_call",
    "is_system_row",
    "strip_mcp_prefix",
    "validate_arguments",
]


def build_tool_id(kind: str, name: str) -> str:
    """Build the natural key `{kind}:{name}`.

    Args:
        kind: Tool kind (`function`, `webhook`, `internal`).
        name: Human label slug.

    Returns:
        The tool natural key (also pinned as the document `id`).
    """
    return TOOL_ID_SEPARATOR.join((kind, name))


def is_system_row(tenant_id: str | None) -> bool:
    """Return whether a row is system-owned (read-only to tenants).

    Args:
        tenant_id: The row's tenant stamp.

    Returns:
        `True` exactly for the system tenant.
    """
    return tenant_id == SYSTEM_TENANT_ID


def _matches_json_type(value: Any, expected: str) -> bool:  # why: JSON values are open-typed
    """Report whether a JSON value satisfies one JSON-Schema `type` name.

    Args:
        value: The argument value.
        expected: The schema `type` (`string`, `integer`, `number`,
            `boolean`, `array`, `object`, `null`).

    Returns:
        `True` when the value matches — and always `True` for an unknown
        type name, which is out of this subset's scope, never a finding.
    """
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def validate_arguments(schema: Mapping[str, Any], args: Mapping[str, Any]) -> list[str]:  # why: JSON-Schema is open
    """Validate call arguments against a JSON-Schema `parameters` contract (subset).

    Covers the object shape tool rows carry: `required` presence plus
    per-key `properties.{name}.type` checks. Anything richer (nesting,
    formats, `allOf`) is out of scope — unknown keywords are ignored, never
    flagged — so this stays a total, dependency-free function.

    Args:
        schema: The tool's `parameters` contract (empty means unconstrained).
        args: The call arguments.

    Returns:
        Problem strings naming schema paths only (never argument values).
    """
    problems: list[str] = []
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in args:
                problems.append(f"missing required: {name}")
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        for name, subschema in properties.items():
            if not isinstance(name, str) or name not in args:
                continue
            expected = subschema.get("type") if isinstance(subschema, dict) else None
            if isinstance(expected, str) and not _matches_json_type(args[name], expected):
                problems.append(f"{name}: expected {expected}")
    return problems


def fingerprint_tool_call(name: str, args: Mapping[str, Any]) -> str:  # why: JSON arguments are free-form
    """Return a stable idempotency fingerprint for one tool call.

    Args:
        name: The canonical tool id.
        args: The call arguments (canonicalized with sorted keys;
            non-serializable values degrade to `str` instead of raising).

    Returns:
        Lowercase SHA-256 hex digest over the name plus canonical arguments.
    """
    canonical = json.dumps(dict(args), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()


def strip_mcp_prefix(namespaced: str) -> str:
    """Strip the MCP registry namespace, returning the canonical tool id.

    Registries surface foreign tools as `mcp:{server}:{canonical}` where the
    canonical id keeps its own `{kind}:{name}` shape — so the prefix plus the
    server segment go, and everything after the server stays intact.

    Args:
        namespaced: Possibly namespaced id (`mcp:{server}:{canonical}`).

    Returns:
        The canonical id when prefixed, else the input unchanged.
    """
    if not namespaced.startswith(MCP_PREFIX):
        return namespaced
    rest = namespaced[len(MCP_PREFIX) :]
    _, separator, canonical = rest.partition(MCP_NAMESPACE_SEPARATOR)
    return canonical if separator else rest
