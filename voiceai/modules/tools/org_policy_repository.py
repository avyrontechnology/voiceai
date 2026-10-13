"""Org sharing-policy persistence: one row per org (spec 0081, Phase-1).

The single-enum rulebook (`user_choice` / `shared_with_override` /
`enforce_shared`) lives in `INTEGRATION_ORG_POLICIES`, keyed by `org_id`
(the document `id` is pinned to it, so rewrites replace instead of
duplicating — the store needs no unique-index seam for the invariant).
Policy rewrites themselves are P2 behaviour; this file persists and
validates shape only.
"""

from __future__ import annotations

import logging

from voiceai.common.logger import get_logger
from voiceai.database.repository import BaseRepository
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.exceptions import ensure_owner_scope
from voiceai.modules.tools.models import OrgPolicy, OwnerScope
from voiceai.modules.tools.static_methods import tenant_allows

__all__ = ["OrgPolicyRepository"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class OrgPolicyRepository:
    """`OrgPolicy` storage over one collection view.

    Args:
        store: The `integration_org_policies` collection view. Tenancy still
            comes from the view; the org match below is the sharing rule.
    """

    def __init__(self, store: BaseRepository[OrgPolicy]) -> None:
        self._store = store

    async def get_policy(self, org_id: str, scope: OwnerScope) -> OrgPolicy | None:
        """Return the active policy row for an org, or `None` (spec 0081, P1).

        Partner-wide manage (`scope.org_id=None`) may read any org policy in
        the tenant view; org-scoped callers read their own org only. Anything
        else — unknown, deleted, foreign, out of scope — reads as missing.

        Args:
            org_id: Home org.
            scope: Who asks (partner always bound — see `ensure_owner_scope`).

        Returns:
            The row with `id` pinned to `org_id`, or `None`.
        """
        ensure_owner_scope(scope)
        row = await self._store.get(org_id)
        if row is None or row.org_id != org_id:
            return None
        if not tenant_allows(row.tenant_id, scope):
            logger.warning("scope tenant mismatch: collection=integration_org_policies id=%s", org_id)
            return None
        if scope.org_id is not None and scope.org_id != org_id:
            logger.debug("policy hidden by scope: org=%s", org_id)
            return None
        return row

    async def save_policy(self, policy: OrgPolicy, scope: OwnerScope) -> OrgPolicy:
        """Insert or replace one org's policy row, pinned to `org_id` (spec 0081, P1).

        Pinning the document id to the org makes rewrites idempotent and
        keeps the one-row-per-org invariant without a unique-index seam.

        Args:
            policy: The row (`id` assigned here to `org_id`, never by callers).
            scope: Who writes (partner always bound; partner-wide or the
                org itself — the flip authorisation itself is P2).

        Returns:
            The persisted row.
        """
        ensure_owner_scope(scope)
        policy.id = policy.org_id
        saved = await self._store.insert(policy)
        logger.info(C.LOG_POLICY_SAVED, saved.org_id, saved.binding_policy)
        return saved
