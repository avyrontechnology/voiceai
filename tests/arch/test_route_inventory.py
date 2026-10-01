"""Route inventory of the single app (spec 0048, Slice B): every path, pinned.

The UI contract is this table. A mount change — a module gaining a route, a platform
router retiring into its module (M6) — is a deliberate edit here, never silent drift.
"""

from __future__ import annotations

from collections import Counter

import pytest
from fastapi import FastAPI

from voiceai.common.constants import API_PREFIX
from voiceai.core.app_factory import create_app
from voiceai.core.environment import Environment

#: FastAPI's own documentation endpoints, the only paths outside the API prefix.
DOCS_PATHS: frozenset[str] = frozenset({"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"})

#: Every `METHOD PATH` the single app serves, module and platform alike (135 entries).
PINNED_ROUTES: frozenset[str] = frozenset(
    {
        "DELETE /api/v1/agent/{agent_id}",
        "DELETE /api/v1/api-keys/{key_id}",
        "DELETE /api/v1/auth/invites/{invite_id}",
        "DELETE /api/v1/auth/teams/{team_id}/members/{user_id}",
        "DELETE /api/v1/auth/users/{user_id}",
        "DELETE /api/v1/batches/{batch_id}",
        "DELETE /api/v1/graphs/{graph_id}",
        "DELETE /api/v1/integrations/{integration_id}",
        "DELETE /api/v1/knowledgebases/{kb_id}",
        "DELETE /api/v1/phone-numbers/{number_id}",
        "DELETE /api/v1/sub-accounts/{sub_id}",
        "DELETE /api/v1/sub-accounts/{sub_id}/members",
        "DELETE /api/v1/talko/partners/{partner_id}",
        "DELETE /api/v1/tools/{tool_id}",
        "DELETE /api/v1/voices/{voice_id}",
        "DELETE /api/v1/webhooks/{webhook_id}",
        "DELETE /api/v1/workflows/{workflow_id}",
        "GET /api/v1/agent/{agent_id}",
        "GET /api/v1/agent/{agent_id}/prompts",
        "GET /api/v1/agents/{agent_id}/vector-config",
        "GET /api/v1/all",
        "GET /api/v1/api-keys",
        "GET /api/v1/auth/events",
        "GET /api/v1/auth/invites",
        "GET /api/v1/auth/me",
        "GET /api/v1/auth/me/teams",
        "GET /api/v1/auth/users",
        "GET /api/v1/batches",
        "GET /api/v1/batches/{batch_id}",
        "GET /api/v1/batches/{batch_id}/executions",
        "GET /api/v1/catalog/modalities",
        "GET /api/v1/catalog/models",
        "GET /api/v1/catalog/providers",
        "GET /api/v1/catalog/voices",
        "GET /api/v1/chat/sessions",
        "GET /api/v1/executions",
        "GET /api/v1/executions/latency/summary",
        "GET /api/v1/executions/stats",
        "GET /api/v1/executions/{execution_id}",
        "GET /api/v1/graphs",
        "GET /api/v1/graphs/{graph_id}",
        "GET /api/v1/graphs/{graph_id}/versions",
        "GET /api/v1/health",
        "GET /api/v1/health/live",
        "GET /api/v1/health/ready",
        "GET /api/v1/inbound/{agent_id}",
        "GET /api/v1/integrations",
        "GET /api/v1/integrations/{integration_id}",
        "GET /api/v1/knowledgebases",
        "GET /api/v1/organization",
        "GET /api/v1/phone-numbers",
        "GET /api/v1/phone-numbers/resolve",
        "GET /api/v1/sub-accounts",
        "GET /api/v1/sub-accounts/{sub_id}",
        "GET /api/v1/talko/partners",
        "GET /api/v1/talko/partners/{partner_id}",
        "GET /api/v1/templates",
        "GET /api/v1/templates/{template_id}",
        "GET /api/v1/tools",
        "GET /api/v1/tools/{tool_id}",
        "GET /api/v1/voices",
        "GET /api/v1/wallet",
        "GET /api/v1/wallet/ledger",
        "GET /api/v1/webhooks",
        "GET /api/v1/workflow-campaigns",
        "GET /api/v1/workflow-campaigns/{campaign_id}",
        "GET /api/v1/workflow-campaigns/{campaign_id}/runs",
        "GET /api/v1/workflow-runs/{run_id}",
        "GET /api/v1/workflows",
        "GET /api/v1/workflows/{workflow_id}",
        "GET /api/v1/workflows/{workflow_id}/versions",
        "PATCH /api/v1/agent/{agent_id}",
        "POST /api/v1/agent",
        "POST /api/v1/api-keys",
        "POST /api/v1/auth/accept",
        "POST /api/v1/auth/invite",
        "POST /api/v1/auth/login",
        "POST /api/v1/auth/logout",
        "POST /api/v1/auth/organizations",
        "POST /api/v1/auth/refresh",
        "POST /api/v1/auth/signup",
        "POST /api/v1/auth/teams",
        "POST /api/v1/auth/teams/{team_id}/members",
        "POST /api/v1/auth/tenants",
        "POST /api/v1/auth/ws-ticket",
        "POST /api/v1/batches",
        "POST /api/v1/batches/{batch_id}/retry-failed",
        "POST /api/v1/batches/{batch_id}/start",
        "POST /api/v1/batches/{batch_id}/stop",
        "POST /api/v1/calls/place",
        "POST /api/v1/calls/simulate",
        "POST /api/v1/chat/{agent_id}",
        "POST /api/v1/graphs",
        "POST /api/v1/graphs/{graph_id}/deploy",
        "POST /api/v1/graphs/{graph_id}/dry-run",
        "POST /api/v1/graphs/{graph_id}/restore/{version_number}",
        "POST /api/v1/graphs/{graph_id}/validate",
        "POST /api/v1/integrations",
        "POST /api/v1/knowledgebases",
        "POST /api/v1/knowledgebases/{kb_id}/attach",
        "POST /api/v1/knowledgebases/{kb_id}/detach",
        "POST /api/v1/organization/reset",
        "POST /api/v1/phone-numbers",
        "POST /api/v1/phone-numbers/{number_id}/assign",
        "POST /api/v1/phone-numbers/{number_id}/unassign",
        "POST /api/v1/sub-accounts",
        "POST /api/v1/sub-accounts/{sub_id}/members",
        "POST /api/v1/talko/partners",
        "POST /api/v1/talko/partners/connect",
        "POST /api/v1/talko/partners/preview",
        "POST /api/v1/talko/partners/{partner_id}/refresh",
        "POST /api/v1/templates/{template_id}/import",
        "POST /api/v1/tools",
        "POST /api/v1/voice/inbound/twilio",
        "POST /api/v1/voices",
        "POST /api/v1/wallet/topup",
        "POST /api/v1/webhooks",
        "POST /api/v1/workflow-campaigns",
        "POST /api/v1/workflow-campaigns/{campaign_id}/start",
        "POST /api/v1/workflow-campaigns/{campaign_id}/stop",
        "POST /api/v1/workflows",
        "POST /api/v1/workflows/{workflow_id}/restore/{version_number}",
        "POST /api/v1/workflows/{workflow_id}/test-run",
        "POST /api/v1/workflows/{workflow_id}/validate",
        "PUT /api/v1/agent/{agent_id}",
        "PUT /api/v1/agents/{agent_id}/vector-config",
        "PUT /api/v1/auth/password",
        "PUT /api/v1/auth/users/{user_id}/role",
        "PUT /api/v1/graphs/{graph_id}",
        "PUT /api/v1/inbound/{agent_id}",
        "PUT /api/v1/integrations/{integration_id}",
        "PUT /api/v1/organization",
        "PUT /api/v1/talko/partners/{partner_id}",
        "PUT /api/v1/tools/{tool_id}",
        "PUT /api/v1/workflows/{workflow_id}",
        "WS /api/v1/chat/v1/{agent_id}",
    }
)


def _routes(app: FastAPI) -> list[tuple[str, str, str]]:
    """Return ``(method, path, handler module)`` for every route, ``WS`` for websockets."""
    rows: list[tuple[str, str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        endpoint = getattr(route, "endpoint", None)
        owner = getattr(endpoint, "__module__", "")
        for method in sorted(getattr(route, "methods", None) or {"WS"}):
            if method in ("HEAD", "OPTIONS"):
                continue
            rows.append((method, path, owner))
    return rows


@pytest.fixture
def single_app(arch_environment: Environment) -> FastAPI:
    return create_app(env=arch_environment)


def test_single_app_serves_exactly_the_pinned_routes(single_app: FastAPI) -> None:
    served = {f"{method} {path}" for method, path, _ in _routes(single_app) if path not in DOCS_PATHS}
    assert served == PINNED_ROUTES, (
        "route table drift (edit PINNED_ROUTES in the same spec):\n"
        + "\n".join(f"+ {row}" for row in sorted(served - PINNED_ROUTES))
        + "\n".join(f"- {row}" for row in sorted(PINNED_ROUTES - served))
    )


def test_every_route_rides_the_api_prefix(single_app: FastAPI) -> None:
    """No bare-path twins survive the cutover (owner decision: clients move to /api/v1)."""
    off_prefix = {path for _, path, _ in _routes(single_app) if not path.startswith(API_PREFIX)}
    assert off_prefix == DOCS_PATHS


def test_no_method_and_path_is_registered_twice(single_app: FastAPI) -> None:
    """A shadowed duplicate would serve one handler and document another."""
    counts = Counter((method, path) for method, path, _ in _routes(single_app))
    assert [key for key, count in counts.items() if count > 1] == []


def test_shared_paths_belong_to_modules_and_the_rest_to_the_platform_router(single_app: FastAPI) -> None:
    owners = {(method, path): owner for method, path, owner in _routes(single_app)}
    assert owners[("POST", f"{API_PREFIX}/calls/place")] == "voiceai.modules.voice.controller"
    assert owners[("POST", f"{API_PREFIX}/calls/simulate")] == "voiceai.platform.router"
    assert owners[("GET", f"{API_PREFIX}/tools")].startswith("voiceai.modules.tools")
    assert owners[("GET", f"{API_PREFIX}/voices")].startswith("voiceai.modules.voices")
    assert owners[("GET", f"{API_PREFIX}/wallet")].startswith("voiceai.modules.wallet")
    assert owners[("GET", f"{API_PREFIX}/phone-numbers")] == "voiceai.platform.router"
    assert owners[("GET", f"{API_PREFIX}/api-keys")] == "voiceai.platform.router"
    platform_owned = sum(1 for owner in owners.values() if owner == "voiceai.platform.router")
    assert platform_owned == 74
