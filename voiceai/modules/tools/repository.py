"""Tool persistence over the `tools` collection (spec 0029, slice 1).

System rows (`tenant_id="system"`) are readable by every tenant through the
system view; tenant rows flow through request-scoped views supplied by the
container. The repository pins `id` to `tool_id` and stamps system rows.
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.pagination import PaginationParams
from voiceai.database.repository import BaseRepository
from voiceai.modules.tools.models import ToolDefinition

__all__ = ["ToolsRepository"]


class ToolsRepository:
    """`ToolDefinition` storage over one collection view.

    Args:
        store: The `tools` collection behind a `BaseRepository` (system view
            for reads of internal tools, scoped view for tenant CRUD — the
            container composes both).
    """

    def __init__(self, store: BaseRepository[ToolDefinition]) -> None:
        self._store = store

    async def save_tool(self, tool: ToolDefinition) -> ToolDefinition:
        """Insert or replace one row, pinned to its natural key.

        Args:
            tool: The tool row (id assigned here, not by callers).

        Returns:
            The persisted row.
        """
        tool.id = tool.tool_id
        return await self._store.insert(tool)

    async def get_tool(self, tool_id: str) -> ToolDefinition | None:
        """Return the active row for a natural key, or `None`.

        Args:
            tool_id: `{kind}:{name}`.

        Returns:
            The row, or `None` when unknown, soft-deleted, or foreign to a
            scoped view.
        """
        return await self._store.get(tool_id)

    async def list_all(self) -> Sequence[ToolDefinition]:
        """Return every active row in this view (scoped views stay in-tenant)."""
        page = await self._store.list(PaginationParams(page=1, page_size=MAX_PAGE_SIZE))
        return list(page.items)

    async def delete_tool(self, tool_id: str) -> bool:
        """Soft-delete one row; foreign rows read as missing."""
        return await self._store.soft_delete(tool_id)
