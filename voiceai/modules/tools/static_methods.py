"""Pure tool helpers: natural keys (spec 0029).

No I/O, no imports beyond stdlib + constants — deterministic and unit-tested.
"""

from __future__ import annotations

from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.modules.tools.constants import TOOL_ID_SEPARATOR

__all__ = ["build_tool_id", "is_system_row"]


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
