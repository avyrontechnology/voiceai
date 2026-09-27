"""Tool business logic: tenant CRUD over system + scoped views (spec 0029).

All business logic, no HTTP types. Reads merge both views (tenant rows win
ties); writes touch the tenant view only, with system rows answering 403.

Live-link (spec 0046, Slice A): `update_tool` bumps `tools_version` and
cascades to attached agents through the injected `AgentToolLink`; `delete_tool`
refuses while references exist; `get_tool_for_attach` gates new attaches on
the deprecation flag while already-attached agents keep resolving.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.errors import ConflictError, NotFoundError
from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.errors import InvalidToolError
from voiceai.modules.tools.exceptions import ensure_system_immutable, ensure_tool_found
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.seed import seed_entries
from voiceai.modules.tools.static_methods import build_tool_id
from voiceai.modules.tools.utils import seed_tools

__all__ = ["AgentToolLink", "ToolsService"]

logger: logging.Logger = get_logger("tools")


@runtime_checkable
class AgentToolLink(Protocol):
    """Tenant-scoped agent reference index for one tool row (spec 0046 Slice A seam).

    No clean seam exists inside the new architecture today: the container wires
    `ToolsService` INTO `AgentService` (agents import tools, never the reverse —
    the reverse would be an import cycle and a layer violation), and
    `AgentDefinitionPort` exposes id-keyed CRUD with no ref index. So the
    cascade and the refuse-delete count coordinate through THIS injected link,
    which the integrator wires to Slice B's re-materialize helper (spec 0046,
    Slice B). Until then `agent_links` stays `None` and both paths degrade
    loudly (warning log, propagation count 0, delete proceeds unverified).

    INTEGRATOR WIRING (in `core/container.py::_build_tools_service`, as a
    per-request Factory so the link closes over the ambient tenant — never a
    Singleton, same pinning hazard as every other scoped view):

        agent_links=agent_adapter,  # closes over Slice B's re-materialize
        # helper + a scan of the ambient tenant's agent dumps for `tool_refs`
        # and webhook `pre_call_webhook_ref` entries naming the tool id.

    Implementer obligations (Slice B + integrator):

    - Same-tenant only: never return or touch another tenant's agents.
    - Bounded: `rematerialize_tool` processes at most `limit` agents per call.
    - Per-agent isolation: one bad agent logs with `error_id` and continues
      (AGENTS.md section 5) — a single corrupt dump must not fail the write.
    - Structural conformance only: implementers do NOT import this Protocol
      (`LlmPort` precedent) — a matching async pair satisfies it.
    """

    async def count_referencing_agents(self, tool_id: str) -> int:
        """Count agents in this tenant referencing `tool_id` (exact, cheap).

        Args:
            tool_id: The `{kind}:{name}` natural key.

        Returns:
            The referencing agent count (both the `tool_refs` and the webhook
            `pre_call_webhook_ref` paths).
        """

    async def rematerialize_tool(self, tool_id: str, *, limit: int) -> int:
        """Re-resolve `tool_id` into every referencing agent (bounded).

        Args:
            tool_id: The `{kind}:{name}` natural key.
            limit: Maximum agents to process this call (fan-out bound).

        Returns:
            The number of agents re-materialized.
        """


class ToolsService:
    """Serve the tool registry: shared reads, tenant writes, immutable system.

    Args:
        system: The `tools` collection as a system-tenant view (internal
            tools + webhook kinds).
        tenant: The `tools` collection as the ambient request tenant's view.
        agent_links: The tenant-scoped agent reference index (spec 0046 Slice A
            seam — see `AgentToolLink`). `None` in test/dev compositions: the
            cascade reports 0 and deletes proceed unverified, both
            warning-logged. Production MUST wire it (integrator, per-request).
    """

    def __init__(
        self,
        system: ToolsRepository,
        tenant: ToolsRepository,
        *,
        agent_links: AgentToolLink | None = None,
    ) -> None:
        self._system = system
        self._tenant = tenant
        self._agent_links = agent_links

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

    async def get_tool_for_attach(self, tool_id: str) -> ToolDefinition:
        """Resolve one row for a NEW attach, refusing deprecated rows (spec 0046).

        Slice B calls this (not `get_tool`) when an agent adds a ref it did not
        carry before: deprecated rows answer 400 naming the tool, while
        already-attached agents keep resolving through `get_tool`
        (grandfathered) and get flagged `stale_deprecated` on read (Slice B).
        Pickers filter deprecated rows client-side.

        Args:
            tool_id: `{kind}:{name}`.

        Returns:
            The row, guaranteed attachable.

        Raises:
            ToolNotFoundError: When neither view holds the id.
            InvalidToolError: When the row is deprecated (HTTP 400, names it).
        """
        row = await self.get_tool(tool_id)
        if row.deprecated:
            raise InvalidToolError(
                f"Tool {tool_id!r} is deprecated and cannot be attached to new agents.",
                details={"tool_id": tool_id, "name": row.name},
            )
        return row

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

    async def update_tool(self, tool_id: str, tool: ToolDefinition) -> tuple[ToolDefinition, int]:
        """Replace one tenant row, bump its version, cascade to attached agents.

        Live-link (spec 0046, Slice A): the stored `tools_version` increments by
        one and every same-tenant agent referencing `tool_id` (via `tool_refs`
        or webhook `pre_call_webhook_ref`) is re-materialized through the
        injected `AgentToolLink` in the same service call — reads keep serving
        the stored snapshot, so the hot path never gains a lookup. Setting
        `deprecated=True` here is stage one of the two-stage stop: the write
        succeeds and the cascade still runs (attached agents keep running,
        flagged `stale_deprecated` on read by Slice B); only NEW attaches gate
        on the flag (see `get_tool_for_attach`).

        INTEGRATOR NOTE: the return shape changed from `ToolDefinition` to
        `(saved, propagated)` — `controller.py::update_tool` must unpack before
        `wire_tool` (controller is outside Slice A ownership, so the one-line
        follow-up rides the integrator). Cascade failures propagate AFTER the
        save: the agents-side loop must isolate per-agent failures (AGENTS.md
        section 5) so one corrupt dump cannot fail the tool write.

        Args:
            tool_id: The `{kind}:{name}` path key (pins the row id).
            tool: The replacement row (id + version re-stamped here, not by
                callers).

        Returns:
            `(saved, propagated)`: the persisted row and the number of agents
            re-materialized (0 when the link is unwired — warning-logged).

        Raises:
            ToolNotFoundError: When no visible row exists.
            ToolForbiddenError: When the row is system-owned.
            InvalidToolError: When `kind` is `internal`.
        """
        existing = await self._system.get_tool(tool_id)
        ensure_system_immutable(existing is not None, tool_id)
        stored: ToolDefinition = ensure_tool_found(await self._tenant.get_tool(tool_id), tool_id)
        self._ensure_writable_kind(tool.kind)
        tool.tool_id = tool_id
        tool.tools_version = stored.tools_version + 1
        saved = await self._tenant.save_tool(tool)
        propagated = await self._propagate_tool_change(tool_id)
        logger.info(
            "tool updated: %s version %d propagated to %d agents",
            tool_id,
            saved.tools_version,
            propagated,
        )
        return saved, propagated

    async def _propagate_tool_change(self, tool_id: str) -> int:
        """Cascade one edit to attached agents, or report the unwired posture."""
        if self._agent_links is None:
            logger.warning(
                "tools cascade unwired: %s version-bumped with no re-materialization",
                tool_id,
            )
            return 0
        return await self._agent_links.rematerialize_tool(tool_id, limit=MAX_PAGE_SIZE)

    async def delete_tool(self, tool_id: str) -> None:
        """Soft-delete one tenant row; refuse while agents reference it (spec 0046).

        Ordering is 403 (system) → 404 (missing/foreign) → 409 (referenced), so
        no reference check ever oracles tenancy or system ownership. The 409
        names the tool and carries the referencing agent count under
        `details["referencing_agents"]` (Slice D pins this key).

        Check-then-act race note: an attach landing between the count and the
        soft-delete strands a ref — accepted for v1 (no distributed lock):
        attach-time resolution reports unknown ids, and the next `update_tool`
        cascade re-materializes whoever remains attached.

        Raises:
            ToolNotFoundError: When no visible row exists.
            ToolForbiddenError: When the row is system-owned.
            ConflictError: While same-tenant agents reference the row (HTTP
                409, with the referencing count).
        """
        existing = await self._system.get_tool(tool_id)
        ensure_system_immutable(existing is not None, tool_id)
        ensure_tool_found(await self._tenant.get_tool(tool_id), tool_id)
        references = await self._count_references(tool_id)
        if references > 0:
            raise ConflictError(
                f"Tool {tool_id!r} is still attached to {references} agent(s); detach it first.",
                details={"tool_id": tool_id, "referencing_agents": references},
            )
        try:
            deleted = await self._tenant.delete_tool(tool_id)
        except NotFoundError:
            deleted = False
        if not deleted:
            ensure_tool_found(None, tool_id)

    async def _count_references(self, tool_id: str) -> int:
        """Count attached agents, or report the unwired posture (delete proceeds)."""
        if self._agent_links is None:
            logger.warning(
                "tools reference check unwired: deleting %s without verifying attaches",
                tool_id,
            )
            return 0
        return await self._agent_links.count_referencing_agents(tool_id)

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
