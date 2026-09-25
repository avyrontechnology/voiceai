"""Tool business logic: tenant CRUD over system + scoped views (spec 0029).

All business logic, no HTTP types. Reads merge both views (tenant rows win
ties); writes touch the tenant view only, with system rows answering 403.
"""

from __future__ import annotations

import logging

from voiceai.common.errors import NotFoundError
from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import InvalidToolError
from voiceai.modules.tools.exceptions import ensure_system_immutable, ensure_tool_found
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.seed import seed_entries
from voiceai.modules.tools.static_methods import build_tool_id
from voiceai.modules.tools.utils import seed_tools

__all__ = ["ToolsService"]

logger: logging.Logger = get_logger("tools")


class ToolsService:
    """Serve the tool registry: shared reads, tenant writes, immutable system.

    Args:
        system: The `tools` collection as a system-tenant view (internal
            tools + webhook kinds).
        tenant: The `tools` collection as the ambient request tenant's view.
    """

    def __init__(self, system: ToolsRepository, tenant: ToolsRepository) -> None:
        self._system = system
        self._tenant = tenant

    async def list_tools(self, *, kind: str | None = None) -> list[ToolDefinition]:
        """List system + own-tenant rows, tenant rows winning id ties.

        Args:
            kind: Narrow to one kind, or all.

        Returns:
            Merged active rows, system first.
        """
        merged: dict[str, ToolDefinition] = {}
        system_ids: set[str] = set()
        for row in await self._system.list_all():
            if kind is None or row.kind == kind:
                merged[row.tool_id] = row
                system_ids.add(row.tool_id)
        for row in await self._tenant.list_all():
            if kind is None or row.kind == kind:
                merged[row.tool_id] = row
        ordered = sorted(merged, key=lambda tool_id: (tool_id not in system_ids, tool_id))
        return [merged[key] for key in ordered]

    async def get_tool(self, tool_id: str) -> ToolDefinition:
        """Resolve one row, tenant view first (override pattern).

        Raises:
            ToolNotFoundError: When neither view holds the id.
        """
        row = await self._tenant.get_tool(tool_id)
        if row is None:
            row = await self._system.get_tool(tool_id)
        found: ToolDefinition = ensure_tool_found(row, tool_id)
        return found

    @staticmethod
    def _ensure_writable_kind(kind: str) -> None:
        """Reject the internal kind (spec 0029).

        Unknown kinds never reach here — the `ToolKind` literal rejects them
        at schema validation. Internal is schema-valid but code-review-owned.

        Raises:
            InvalidToolError: When `kind` is `internal`.
        """
        if kind == C.TOOL_KIND_INTERNAL:
            raise InvalidToolError(
                "Internal tools are curated in code review, not created via API.",
                details={"kind": kind},
            )

    async def create_tool(self, tool: ToolDefinition) -> ToolDefinition:
        """Store one tenant row (open kinds only — see below).

        Args:
            tool: The row (id + tenant stamped here, not by callers).

        Returns:
            The persisted row.

        Raises:
            InvalidToolError: When `kind` is `internal` (code review owns it)
                or anything outside `function`/`webhook`.
        """
        self._ensure_writable_kind(tool.kind)
        tool.tool_id = build_tool_id(tool.kind, tool.name)
        return await self._tenant.save_tool(tool)

    async def update_tool(self, tool_id: str, tool: ToolDefinition) -> ToolDefinition:
        """Replace one tenant row; system rows answer 403.

        Raises:
            ToolNotFoundError: When no visible row exists.
            ToolForbiddenError: When the row is system-owned.
        """
        existing = await self._system.get_tool(tool_id)
        ensure_system_immutable(existing is not None, tool_id)
        ensure_tool_found(await self._tenant.get_tool(tool_id), tool_id)
        self._ensure_writable_kind(tool.kind)
        tool.tool_id = tool_id
        return await self._tenant.save_tool(tool)

    async def delete_tool(self, tool_id: str) -> None:
        """Soft-delete one tenant row; system rows answer 403, foreign 404.

        Raises:
            ToolNotFoundError: When no visible row exists.
            ToolForbiddenError: When the row is system-owned.
        """
        existing = await self._system.get_tool(tool_id)
        ensure_system_immutable(existing is not None, tool_id)
        try:
            deleted = await self._tenant.delete_tool(tool_id)
        except NotFoundError:
            deleted = False
        if not deleted:
            ensure_tool_found(None, tool_id)

    async def seed(self) -> int:
        """Load the curated internal rows idempotently (insert-or-replace).

        Returns:
            The number of rows written.
        """
        return await seed_tools(self._system)

    async def ensure_seeded(self) -> dict[str, int]:
        """Sync the system view to the seed (boot path, catalog precedent).

        Returns:
            `{"inserted": n, "updated": n, "current": n}`.
        """
        stored = {row.tool_id: row for row in await self._system.list_all()}
        inserted = 0
        updated = 0
        for entry in seed_entries():
            existing = stored.get(entry.tool_id)
            if existing is None:
                await self._system.save_tool(entry)
                inserted += 1
            elif existing.tools_version < entry.tools_version:
                await self._system.save_tool(entry)
                updated += 1
        result = {"inserted": inserted, "updated": updated, "current": len(stored)}
        logger.info(
            "tools sync: %d inserted, %d updated, %d current",
            inserted,
            updated,
            len(stored),
        )
        return result
