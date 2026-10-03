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

#: Integration routes (spec 0058): templates read curated provider rows,
#: credentials carry vault envelopes (metadata-only on reads), links bind
#: providers to agents, DCR registers outbound OAuth clients.
TEMPLATES_PATH: Final[str] = "/integration-templates"
TEMPLATE_ITEM_PATH: Final[str] = "/integration-templates/{provider}"
TEMPLATE_SYNC_PATH: Final[str] = "/integration-templates/{provider}/sync-tools"
CREDENTIALS_PATH: Final[str] = "/partner-credentials"
CREDENTIAL_ITEM_PATH: Final[str] = "/partner-credentials/{credential_id}"
INTEGRATIONS_PATH: Final[str] = "/partner-integrations"
INTEGRATION_ITEM_PATH: Final[str] = "/partner-integrations/{integration_id}"
DCR_REGISTER_PATH: Final[str] = "/integrations/dcr-register"

#: Wire keys for credential/integration payloads (identifiers only on reads).
CREDENTIALS_KEY: Final[str] = "credentials"
SECRET_ONCE_KEY: Final[str] = "secret_once"
REPLACED_KEY: Final[str] = "replaced"
CLIENT_SECRET_ONCE_KEY: Final[str] = "client_secret_once"
CLIENT_ID_KEY: Final[str] = "client_id"
ISSUER_URL_KEY: Final[str] = "issuer_url"

#: MCP server pilot (spec 0057): single Streamable-HTTP endpoint (JSON-only).
#: Auth reuses `platform:read` (existing scope — `mcp:invoke` lands with OAuth in S2).
MCP_PATH: Final[str] = "/mcp"
MCP_SESSION_ID_HEADER: Final[str] = "Mcp-Session-Id"
MCP_PROTOCOL_VERSION: Final[str] = "2026-07-28"
MCP_SERVER_NAME: Final[str] = "otobaai-tools"
MCP_LIST_PAGE_SIZE: Final[int] = 50
MCP_LIST_PAGE_MAX: Final[int] = 50

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
AGENT_QUERY_DESCRIPTION: Final[str] = "Narrow to one agent's links."

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

#: MCP registry namespace (spec 0056): foreign tools surface as
#: `mcp:{server}:{tool}`; the executor strips to the canonical id.
MCP_PREFIX: Final[str] = "mcp:"
MCP_NAMESPACE_SEPARATOR: Final[str] = ":"

#: The 401 path retries exactly once after cache-invalidate + refresh (spec 0056).
RETRY_ONCE: Final[int] = 1

#: Allowlist-denial message (identifiers only — never arguments or payloads).
ALLOWLIST_DENY_TEMPLATE: Final[str] = "Tool {tool_id!r} is not attached to agent {agent_id!r}."

#: Executor bounds (spec 0056): audit summaries stay short, response-text
#: fallbacks stay bounded, GET never carries a body.
EXECUTOR_SUMMARY_MAX_CHARS: Final[int] = 500
EXECUTOR_RESPONSE_MAX_CHARS: Final[int] = 2000
EXECUTOR_GET_METHOD: Final[str] = "GET"

#: Result-payload keys for the internal route and text fallbacks.
EXECUTOR_DATA_TOOL: Final[str] = "tool"
EXECUTOR_DATA_ARGUMENTS: Final[str] = "arguments"
EXECUTOR_DATA_FINGERPRINT: Final[str] = "call_fingerprint"
EXECUTOR_TEXT_KEY: Final[str] = "text"

#: Human summaries (status codes and timings only — never bodies or secrets).
EXECUTOR_HTTP_SUMMARY_TEMPLATE: Final[str] = "HTTP {status} in {ms}ms"
EXECUTOR_MCP_SUMMARY_TEMPLATE: Final[str] = "mcp {tool} in {ms}ms"
EXECUTOR_INTERNAL_SUMMARY_TEMPLATE: Final[str] = "internal:{name} in {ms}ms"
EXECUTOR_TIMEOUT_SUMMARY_TEMPLATE: Final[str] = "tool timed out after {timeout}s"
EXECUTOR_UNREACHABLE_SUMMARY: Final[str] = "tool endpoint unreachable"

#: Credential-cache seam (spec 0056 Phase-1; vault rows land in Phase-2).
AUTH_CACHE_KEY_PREFIX: Final[str] = "tools:auth:"
AUTH_CACHE_TTL_S: Final[int] = 300
AUTH_CACHE_TIMEOUT_S: Final[int] = 2
AUTH_BEARER_SCHEME: Final[str] = "Bearer"
AUTH_AUTHORIZATION_HEADER: Final[str] = "Authorization"
AUTH_API_KEY_HEADER: Final[str] = "API-KEY"
AUTH_TOKEN_FIELD: Final[str] = "token"  # noqa: S105 -- credential-envelope key name, not a secret
AUTH_API_KEY_FIELD: Final[str] = "api_key"
AUTH_HEADERS_FIELD: Final[str] = "headers"
AUTH_REF_FIELD: Final[str] = "auth_ref"

#: Log detail keys (identifiers only — never argument values or URLs).
LOG_KEY_TOOL_ID: Final[str] = "tool_id"
LOG_KEY_AGENT_ID: Final[str] = "agent_id"

#: Operator log lines (identifiers and timings only — never secrets or bodies).
LOG_TOOL_EXECUTED: Final[str] = "tool executed: %s success=%s in %dms"
LOG_TOOL_REFUSED: Final[str] = "tool refused: %s ref %s"
LOG_TOOL_TIMED_OUT: Final[str] = "tool timed out: %s ref %s"
LOG_TOOL_UNREACHABLE: Final[str] = "tool unreachable: %s (%s)"
LOG_AUTH_CACHE_DEGRADED: Final[str] = "tools auth cache degraded (%s)"
LOG_WRITE_FAILED: Final[str] = "tool log write failed (%s)"

#: Credential shapes the vault stores (spec 0059 auth model; canonical home —
#: `vault_models` re-exports these names, integrator-merged Phase-2).
CredentialType = Literal["none", "api_key", "bearer", "basic", "oauth2", "custom"]
CREDENTIAL_TYPE_NONE: Final[Literal["none"]] = "none"
CREDENTIAL_TYPE_OAUTH2: Final[Literal["oauth2"]] = "oauth2"
CREDENTIAL_TYPE_API_KEY: Final[Literal["api_key"]] = "api_key"
CREDENTIAL_TYPE_BEARER: Final[Literal["bearer"]] = "bearer"
CREDENTIAL_TYPE_BASIC: Final[Literal["basic"]] = "basic"
CREDENTIAL_TYPE_CUSTOM: Final[Literal["custom"]] = "custom"

#: Integration transports (spec 0060): generic-HTTP manual schemas vs
#: remote-MCP auto-discovery. One vocabulary for rows, sync, executor, UI.
TransportType = Literal["http", "mcp"]
TRANSPORT_HTTP: Final[Literal["http"]] = "http"
TRANSPORT_MCP: Final[Literal["mcp"]] = "mcp"

#: `auth_ref` pointer format pin (spec 0059): `cred:<credential_id>`.
#: Anything else fails closed in the store (never a bare call).
AUTH_REF_PREFIX: Final[str] = "cred:"

#: api_key placement + defaults (spec 0059): header or query under `name`.
AUTH_LOCATION_HEADER: Final[str] = "header"
AUTH_LOCATION_QUERY: Final[str] = "query"
AUTH_LOCATION_FIELD: Final[str] = "location"
AUTH_NAME_FIELD: Final[str] = "name"
AUTH_USERNAME_FIELD: Final[str] = "username"
AUTH_PASSWORD_FIELD: Final[str] = "password"
DEFAULT_API_KEY_HEADER: Final[str] = "X-API-Key"

#: Sealed-material JSON keys (values only — names live in `auth_config`).
AUTH_SECRET_FIELD: Final[str] = "secret"

#: Default lifecycle state for a freshly linked integration.
INTEGRATION_STATUS_ACTIVE: Final[str] = "active"

#: Document field every vault lookup filters on (code constant, never user input).
VAULT_PROVIDER_FIELD: Final[str] = "provider"
