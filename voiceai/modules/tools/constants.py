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

#: Scope gates (spec 0049): reads need `platform:read`, writes `platform:write`
#: — the scopes the retired platform tools routes used.
SCOPE_PLATFORM_READ: Final[str] = "platform:read"
SCOPE_PLATFORM_WRITE: Final[str] = "platform:write"

#: Operator-facing 403 message (the scope name is interpolated, never payloads).
SCOPE_REQUIRED_TEMPLATE: Final[str] = "Requires {scope} scope"

#: Request header carrying bearer credentials (the gate's fallback to the cookie).
AUTHORIZATION_HEADER: Final[str] = "authorization"

#: Delete acknowledgement body: `{"state": "deleted"}`.
STATE_KEY: Final[str] = "state"
STATE_DELETED: Final[str] = "deleted"

#: OpenAPI descriptions for the request surface.
KIND_FIELD_DESCRIPTION: Final[str] = "`function` or `webhook` (`internal` rejected)."
KIND_QUERY_DESCRIPTION: Final[str] = "Narrow to one kind."

#: Default HTTP method for the endpoint binding.
DEFAULT_TOOL_METHOD: Final[str] = "POST"

#: Default endpoint timeout for tool calls (seconds), with the accepted range.
TOOL_TIMEOUT_S: Final[int] = 10
TOOL_TIMEOUT_MIN_S: Final[int] = 1
TOOL_TIMEOUT_MAX_S: Final[int] = 120

#: `to_definition` backstop (spec 0049): the 400 raised when the row model rejects
#: a payload the request schema accepted; `details[DETAIL_KEY_FIELDS]` carries the
#: offending field paths only, never pydantic text.
DEFINITION_REJECTED_MESSAGE: Final[str] = "Tool payload does not form a valid tool definition."
DETAIL_KEY_FIELDS: Final[str] = "fields"

#: Natural-key separator for `tool_id` (`{kind}:{name}`).
TOOL_ID_SEPARATOR: Final[str] = ":"

#: Seed revision stamped on every row (drift visibility, catalog precedent).
TOOLS_VERSION: Final[int] = 1
