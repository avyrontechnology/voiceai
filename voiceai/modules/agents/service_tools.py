"""Agent shared-tool machinery: ref resolution, materialization, live-link (spec 0046).

`AgentToolsMixin` owns everything the service does with shared tool/webhook
refs: attach-time resolution + SSRF gating, version-stamp bookkeeping, and the
read-only staleness flags. It composes into `service.AgentService`, which
keeps agent CRUD, prompts, extraction, catalog validation, and PATCH
orchestration. Split cited by spec 0046 (integrator): the live-link +
parity slices grew `service.py` past the 800-line canonical budget.

The mixin is self-sufficient for type-checking: `_tools`/`_logger` are
declared here and assigned by `AgentService.__init__`; every other name it
touches is module-level in this file.
"""

from __future__ import annotations

from asyncio import to_thread
from logging import Logger
from typing import Any, Final

from voiceai.common.security import is_safe_outbound_url
from voiceai.modules.agents.constants import TASKS_KEY

__all__ = ["AgentToolsMixin"]

_LOG_TOOLS_UNWIRED: Final[str] = "agent tool refs skipped: tools unwired"

# Live-link stamps + read flags (spec 0046 slice B): source versions ride beside
# materialized tools inside each `api_tools` dump; reads surface staleness only.
_TOOL_SOURCE_VERSIONS_KEY: Final[str] = "tool_source_versions"
_PRE_CALL_WEBHOOK_REF_KEY: Final[str] = "pre_call_webhook_ref"
_STALE_DEPRECATED_KEY: Final[str] = "stale_deprecated"
_TOOL_VERSION_DRIFT_KEY: Final[str] = "tool_version_drift"

# Attach-time endpoint keys the SSRF parity gate walks (spec 0046 slice C).
_URL_KEY: Final[str] = "url"
_PRE_CALL_WEBHOOK_URL_KEY: Final[str] = "pre_call_webhook_url"

# Client-visible SSRF problem suffix: names the failing path, never the URL (§4 opacity).
_ENDPOINT_SAFETY_MESSAGE: Final[str] = "endpoint failed safety validation"


async def _is_url_safe(url: str) -> bool:
    """SSRF pre-flight for an attach-time endpoint (spec 0029 slice 2).

    The guard blocks on DNS, so it runs off the event loop (spec 0001).
    Module-level (not a method) so tests monkeypatch the seam without
    network — the resolver itself stays injectable inside the guard.

    Moved here from `service.py` by the spec-0046 split (integrator);
    test patch targets moved with it (`service_tools._is_url_safe`).

    Args:
        url: The absolute endpoint URL about to persist on an agent record.

    Returns:
        `True` only when the URL is safe to request.
    """
    return await to_thread(is_safe_outbound_url, url)


class AgentToolsMixin:
    """Shared tool/webhook ref machinery, mixed into `AgentService`."""

    _tools: Any  # why: ToolsService; None keeps test compositions ref-free
    _logger: Logger

    async def rematerialize_agent_tools(self, data: dict[str, Any]) -> list[str]:
        """Re-materialize every shared ref on one agent dump, idempotently (spec 0046 slice B).

        The importable seam behind both attach-time writes (`_resolve_tool_refs`,
        via `_validate_providers`) and Slice A's `update_tool` cascade: it
        re-resolves each `tool_ref`/`pre_call_webhook_ref` into the embedded
        snapshot (embedded entries still win ties), refreshes the
        `tool_source_versions` stamps, and reports problems without raising.
        Calling it twice over unchanged tool rows yields byte-identical
        snapshots and stamps. It never touches the store — the caller persists
        the mutated dump.

        Args:
            data: The agent definition dump, mutated in place.

        Returns:
            Problem strings (empty when every ref resolved).
        """
        return await self._resolve_tool_refs(data)

    @staticmethod
    def _api_tools_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:  # why: task dumps are free-form JSON
        """Collect every task's `api_tools` mapping from one agent dump.

        Args:
            data: The agent definition dump (read-only here).

        Returns:
            The live `api_tools` dicts in task order (empty when none).
        """
        blocks: list[dict[str, Any]] = []  # why: task dumps are free-form JSON
        for task in data.get(TASKS_KEY, []) or []:
            if not isinstance(task, dict):
                continue
            tools_config = task.get("tools_config")
            if not isinstance(tools_config, dict):
                continue
            api_tools = tools_config.get("api_tools")
            if isinstance(api_tools, dict):
                blocks.append(api_tools)
        return blocks

    @staticmethod
    def _attached_refs(api_tools: dict[str, Any]) -> list[str]:
        """List every shared ref one `api_tools` block attaches (deduped, in order).

        Covers function `tool_refs` and per-entry `pre_call_webhook_ref` values;
        non-string values are ignored (schema validation owns the shape).

        Args:
            api_tools: The task's `api_tools` mapping (read-only here).

        Returns:
            The attached ref ids, each once, `tool_refs` first.
        """
        refs: list[str] = []
        for ref in api_tools.get("tool_refs", []) or []:
            if isinstance(ref, str) and ref and ref not in refs:
                refs.append(ref)
        params = api_tools.get("tools_params")
        if not isinstance(params, dict):
            return refs
        for entry in params.values():
            if not isinstance(entry, dict):
                continue
            ref = entry.get(_PRE_CALL_WEBHOOK_REF_KEY)
            if isinstance(ref, str) and ref and ref not in refs:
                refs.append(ref)
        return refs

    async def _stamp_tool_source_versions(self, data: dict[str, Any]) -> None:
        """Stamp each attached ref's live `tools_version` beside the materialized tools.

        Additive and idempotent: the `tool_source_versions` map is rebuilt from
        the currently attached refs on every call (detached refs are pruned,
        unknown refs are skipped — their problems already reported), so legacy
        rows gain stamps on their first re-materialization and repeat calls
        over unchanged rows rewrite identical values.

        Args:
            data: The agent definition dump, mutated in place.
        """
        from voiceai.modules.tools.errors import ToolNotFoundError

        if self._tools is None:
            return
        for api_tools in self._api_tools_blocks(data):
            refs = self._attached_refs(api_tools)
            if not refs:
                api_tools.pop(_TOOL_SOURCE_VERSIONS_KEY, None)
                continue
            stamps: dict[str, int] = {}
            for ref in refs:
                try:
                    row = await self._tools.get_tool(ref)
                except ToolNotFoundError:
                    continue
                version = getattr(row, "tools_version", None)
                if isinstance(version, int):
                    stamps[ref] = version
            if stamps:
                api_tools[_TOOL_SOURCE_VERSIONS_KEY] = stamps
            else:
                api_tools.pop(_TOOL_SOURCE_VERSIONS_KEY, None)

    async def _livelink_flags(self, data: dict[str, Any]) -> dict[str, Any]:  # why: flags are read-only metadata
        """Compute the live-link staleness flags for one stored dump (spec 0046 slice B).

        Compares each attached ref against its live row: a deprecated row raises
        `stale_deprecated`, a stamped version behind the live `tools_version`
        lists the ref under `tool_version_drift`. Rows without stamps are
        legacy (pre-0046) and read as always-fresh for drift — deprecation is
        still live-checked. Unknown refs are skipped (writes own that error);
        the input is never mutated and nothing is persisted here.

        Args:
            data: The stored agent definition dump (read-only).

        Returns:
            The flag mapping (empty when fresh — callers then read byte-identical).
        """
        from voiceai.modules.tools.errors import ToolNotFoundError

        if self._tools is None:
            return {}
        stale = False
        drift: list[str] = []
        for api_tools in self._api_tools_blocks(data):
            raw_stamps = api_tools.get(_TOOL_SOURCE_VERSIONS_KEY)
            stamps: dict[Any, Any] = (
                raw_stamps if isinstance(raw_stamps, dict) else {}
            )  # why: stamps are free-form JSON
            for ref in self._attached_refs(api_tools):
                try:
                    row = await self._tools.get_tool(ref)
                except ToolNotFoundError:
                    continue
                if bool(getattr(row, "deprecated", False)):
                    stale = True
                live = getattr(row, "tools_version", None)
                pinned = stamps.get(ref)
                if isinstance(live, int) and isinstance(pinned, int) and pinned != live and ref not in drift:
                    drift.append(ref)
        flags: dict[str, Any] = {}  # why: read-only staleness metadata, never a trust root
        if stale:
            flags[_STALE_DEPRECATED_KEY] = True
        if drift:
            flags[_TOOL_VERSION_DRIFT_KEY] = sorted(drift)
        return flags

    @staticmethod
    def _with_livelink_flags(
        data: dict[str, Any],
        flags: dict[str, Any],  # why: agent dumps and flag maps are free-form JSON
    ) -> dict[str, Any]:  # why: reads serve raw config dicts
        """Return `data` with read flags surfaced, never mutating the stored object.

        Args:
            data: The stored agent definition dump.
            flags: The `_livelink_flags` result for it.

        Returns:
            `data` itself when fresh (byte-identical), else a shallow copy
            carrying the flags as top-level read-only metadata.
        """
        if not flags:
            return data
        annotated = dict(data)
        annotated.update(flags)
        return annotated

    async def _resolve_tool_refs(self, data: dict[str, Any]) -> list[str]:
        """Resolve shared tool/webhook refs into embedded config (spec 0029 slice 2).

        Runs BEFORE catalog validation so materialized rows walk like embedded
        ones. Embedded entries win ties (local override); refs stay on the
        record for refresh provenance. Unknown or foreign ids report with the
        visible ids (no oracle — foreign reads as missing); unwired
        compositions skip with a warning. Ref-attached endpoints pass the
        SSRF pre-flight (fail-closed); pre-existing embedded URLs are
        grandfathered, never re-checked here.

        Args:
            data: The agent definition dump, mutated in place.

        Returns:
            Problem strings (empty when every ref resolved).
        """
        from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError

        if self._tools is None:
            self._logger.warning(_LOG_TOOLS_UNWIRED)
            return []
        problems: list[str] = []
        valid_ids: list[str] | None = None
        for position, task in enumerate(data.get(TASKS_KEY, []) or []):
            if not isinstance(task, dict):
                continue
            tools_config = task.get("tools_config")
            if not isinstance(tools_config, dict):
                continue
            api_tools = tools_config.get("api_tools")
            if not isinstance(api_tools, dict):
                continue
            where = f"tasks[{position}].api_tools"
            for ref in api_tools.get("tool_refs", []) or []:
                try:
                    row = await self._tools.get_tool_for_attach(ref)
                except ToolNotFoundError:
                    if valid_ids is None:
                        valid_ids = sorted({entry.tool_id for entry in await self._tools.list_tools()})
                    suffix = f" (valid: {', '.join(valid_ids)})" if valid_ids else ""
                    problems.append(f"{where}: unknown tool ref {ref!r}{suffix}")
                    continue
                except InvalidToolError:
                    problems.append(f"{where}: deprecated tool ref {ref!r} (retired, pick a live tool)")
                    continue
                problem = await self._materialize_tool_ref(api_tools, row, where)
                if problem is not None:
                    problems.append(problem)
            params = api_tools.get("tools_params")
            if isinstance(params, dict):
                for name, entry in params.items():
                    if not isinstance(entry, dict):
                        continue
                    new_problems = await self._resolve_webhook_ref(entry, f"{where}.tools_params.{name}")
                    if new_problems and "unknown webhook ref" in new_problems[0]:
                        if valid_ids is None:
                            valid_ids = sorted({row.tool_id for row in await self._tools.list_tools()})
                        if valid_ids:
                            new_problems[0] += f" (valid: {', '.join(valid_ids)})"
                    problems.extend(new_problems)
        # Live-link (spec 0046 slice B): refresh source-version stamps on the same
        # write path, so attach-time and cascade share one code path.
        await self._stamp_tool_source_versions(data)
        return problems

    async def _materialize_tool_ref(self, api_tools: dict[str, Any], row: Any, where: str) -> str | None:
        """Merge one shared row into embedded tools/params (embedded wins ties).

        Args:
            api_tools: The task's `api_tools` mapping, mutated in place.
            row: The resolved tool row.
            where: The `tasks[N].api_tools` path for problem strings.

        Returns:
            A problem string when the row's endpoint fails the SSRF
            pre-flight, else `None`. Pure-internal rows (no endpoint) stamp
            the function definition only — no null URL is ever persisted.
        """
        tools = api_tools.get("tools")
        if tools is None:
            tools = []
            api_tools["tools"] = tools
        if isinstance(tools, list):
            names = {
                (item.get("function") or {}).get("name") if isinstance(item, dict) else None for item in tools
            }
            if row.name not in names:
                tools.append(
                    {
                        "type": "function",
                        "function": {"name": row.name, "description": row.description, "parameters": row.parameters},
                    }
                )
        params = api_tools.get("tools_params")
        if isinstance(params, dict) and row.name not in params:
            if row.url is None:
                return None
            if not await _is_url_safe(row.url):
                return f"{where}: tool {row.name!r} endpoint failed safety validation"
            params[row.name] = {"url": row.url, "method": row.method}
        return None

    async def _resolve_webhook_ref(self, entry: dict[str, Any], where: str) -> list[str]:
        """Stamp one tools_params entry from its webhook ref (spec 0029 slice 2).

        `None` means absent (this runs on the validated dump, where every
        APIParams field materializes — a `None` was never user-supplied). Any
        other value, even falsy, is the per-agent override and the shared row
        is never touched.

        Args:
            entry: The params entry, mutated in place.
            where: The `tasks[N].api_tools.tools_params.<name>` path.

        Returns:
            Problem strings (empty when the entry resolved or had no ref).
        """
        from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError

        ref = entry.get("pre_call_webhook_ref")
        if not ref:
            return []
        if self._tools is None:
            self._logger.warning(_LOG_TOOLS_UNWIRED)
            return []
        try:
            row = await self._tools.get_tool_for_attach(ref)
        except ToolNotFoundError:
            return [f"{where}: unknown webhook ref {ref!r}"]
        except InvalidToolError:
            return [f"{where}: deprecated webhook ref {ref!r} (retired, pick a live webhook)"]
        if entry.get("pre_call_webhook_url") is None:
            if row.url is None:
                return [f"{where}: webhook {row.name!r} has no endpoint"]
            if not await _is_url_safe(row.url):
                return [f"{where}: webhook {row.name!r} endpoint failed safety validation"]
            entry["pre_call_webhook_url"] = row.url
        if entry.get("pre_call_webhook_param") is None:
            entry["pre_call_webhook_param"] = dict(row.params_template)
        return []

    async def _gate_embedded_endpoints(self, data: dict[str, Any]) -> list[str]:
        """Gate embedded (non-ref) endpoints through the SSRF pre-flight (spec 0046 slice C).

        Ref-attached URLs are gated where they materialize; caller-supplied
        `tools_params` URLs never pass through that path, so they are gated here
        with the same `_is_url_safe` pre-flight (off-loop, fail-closed).
        Problems name the dotted path
        (`tasks[N].api_tools.tools_params.<name>.<key>`) and never echo the URL
        (§4 error opacity). Stored rows are never scanned — this runs only on the
        attach/re-save write path, so grandfathered records keep serving until
        they are re-saved.

        Args:
            data: The agent definition dump, read-only (never mutated).

        Returns:
            Problem strings (empty when every embedded endpoint is safe or absent).
        """
        problems: list[str] = []
        for position, task in enumerate(data.get(TASKS_KEY, []) or []):
            if not isinstance(task, dict):
                continue
            tools_config = task.get("tools_config")
            if not isinstance(tools_config, dict):
                continue
            api_tools = tools_config.get("api_tools")
            if not isinstance(api_tools, dict):
                continue
            params = api_tools.get("tools_params")
            if not isinstance(params, dict):
                continue
            for name, entry in params.items():
                if not isinstance(entry, dict):
                    continue
                where = f"tasks[{position}].api_tools.tools_params.{name}"
                url = entry.get(_URL_KEY)
                if isinstance(url, str) and url and not await _is_url_safe(url):
                    problems.append(f"{where}.{_URL_KEY}: {_ENDPOINT_SAFETY_MESSAGE}")
                hook_url = entry.get(_PRE_CALL_WEBHOOK_URL_KEY)
                if isinstance(hook_url, str) and hook_url and not await _is_url_safe(hook_url):
                    problems.append(f"{where}.{_PRE_CALL_WEBHOOK_URL_KEY}: {_ENDPOINT_SAFETY_MESSAGE}")
        return problems
