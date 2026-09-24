"""Offline helper: census Upstash Redis key families and map them onto Atlas collections.

Strangler ops tool (spec 0001): the legacy platform kept session-adjacent state in
Upstash while the greenfield auth store (``voiceai.modules.auth``) is the Atlas
system of record. This module declares *which* Redis families exist (``FAMILIES``)
and *which* of them migrate (``MIGRATED``) so an operator-run backfill can move
durable rows without touching ephemera.

Ephemeral families — live sessions, login-throttle counters, revocation-cache
entries — are censused but NEVER migrated by design: they are TTL state that a
backfill must not resurrect (``test_sessions_and_throttle_are_never_migrated``).

Offline only: no network, no credentials. Importing this module has no side
effects; the actual copy loop lives in the operator runbook, not here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

__all__ = ["FAMILIES", "MIGRATED", "_coerce_dates", "_model_for"]

#: (Redis glob pattern, human description). Every family the platform ever wrote.
FAMILIES: Final[tuple[tuple[str, str], ...]] = (
    ("platform:v1:users:*", "legacy user rows (durable)"),
    ("platform:v1:invites:*", "legacy invite rows (durable)"),
    ("platform:v1:apikeys:*", "legacy API-key rows (durable)"),
    ("platform:v1:events:*", "legacy auth-event rows (durable)"),
    ("platform:v1:sessions:*", "live session cache (ephemeral TTL — never migrate)"),
    ("platform:v1:revoked:*", "revocation cache entries (ephemeral TTL — never migrate)"),
    ("auth:throttle:*", "login-throttle counters (ephemeral TTL — never migrate)"),
    ("auth:denied:*", "revocation denylist cache (ephemeral TTL — never migrate)"),
)

#: (Redis glob pattern, Atlas collection, greenfield model name, natural key).
#: Only durable families appear here; every pattern must be censused in FAMILIES.
MIGRATED: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("platform:v1:users:*", "users", "User", "user_id"),
    ("platform:v1:invites:*", "invites", "Invite", "invite_id"),
    ("platform:v1:apikeys:*", "api_keys", "ApiKey", "key_id"),
    ("platform:v1:events:*", "auth_events", "AuthEvent", "event_id"),
)

#: Payload keys ending in this suffix carry ISO timestamps worth coercing for BSON.
_AT_SUFFIX: Final[str] = "_at"


def _model_for(model_name: str) -> Any:  # why: the table names models, callers need the class
    """Resolve a migration-table model name to its greenfield document class.

    Args:
        model_name: One of the model names in ``MIGRATED``.

    Returns:
        The document class (identity-compared by the contract tests).

    Raises:
        KeyError: When the name is not a migrated model — a loud failure beats a
            silent family, so the census test catches table drift.
    """
    from voiceai.modules.auth.models.apikey import ApiKey
    from voiceai.modules.auth.models.audit import AuthEvent
    from voiceai.modules.auth.models.invite import Invite
    from voiceai.modules.auth.models.user import User

    return {"User": User, "Invite": Invite, "ApiKey": ApiKey, "AuthEvent": AuthEvent}[model_name]


def _coerce_dates(payload: dict[str, Any]) -> dict[str, Any]:  # why: Redis payloads are untyped JSON
    """Coerce ISO ``*_at`` strings to datetimes; leave everything else untouched.

    Args:
        payload: One decoded Redis hash (string keys, JSON scalar values).

    Returns:
        A copy with parseable ``*_at`` values replaced by ``datetime`` objects.
        Unparseable values pass through unchanged — garbage must never fail a
        backfill, it is quarantined downstream, not dropped here.
    """
    coerced: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and key.endswith(_AT_SUFFIX):
            try:
                coerced[key] = datetime.fromisoformat(value)
                continue
            except ValueError:
                pass
        coerced[key] = value
    return coerced
