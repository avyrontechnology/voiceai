"""Every literal the tools module uses (AGENTS.md rule 1b)."""

from __future__ import annotations

from typing import Final, Literal

#: Module name for the logger, router tags and registry entry.
MODULE_NAME: Final[str] = "tools"

#: Router tag for the tool endpoints.
TOOLS_TAG: Final[str] = "Tools"

#: Tool kinds (spec 0029, Phase B).
ToolKind = Literal["function", "webhook", "internal"]
TOOL_KIND_FUNCTION: Final[Literal["function"]] = "function"
TOOL_KIND_WEBHOOK: Final[Literal["webhook"]] = "webhook"
TOOL_KIND_INTERNAL: Final[Literal["internal"]] = "internal"

#: Route paths (mounted under the API prefix by the app factory).
TOOLS_PATH: Final[str] = "/tools"
TOOL_ITEM_PATH: Final[str] = "/tools/{tool_id}"

#: Default endpoint timeout for tool calls (seconds).
TOOL_TIMEOUT_S: Final[int] = 10

#: Natural-key separator for `tool_id` (`{kind}:{name}`).
TOOL_ID_SEPARATOR: Final[str] = ":"

#: Seed revision stamped on every row (drift visibility, catalog precedent).
TOOLS_VERSION: Final[int] = 1
