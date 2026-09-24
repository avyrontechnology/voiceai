"""Offline helper: first-run seeder roster (spec 0001).

One row per role on non-routable ``example.com`` addresses so a fresh workspace
can bootstrap an owner, an admin, a member, and a viewer without touching real
mailboxes. Offline only: importing this module has no side effects; the actual
insert loop lives in the operator runbook, not here.
"""

from __future__ import annotations

from typing import Final

__all__ = ["ROSTER"]

#: (email, display name, role). Roles cover every rank exactly once.
ROSTER: Final[tuple[tuple[str, str, str], ...]] = (
    ("owner@example.com", "Owner", "owner"),
    ("admin@example.com", "Admin", "admin"),
    ("member@example.com", "Member", "member"),
    ("viewer@example.com", "Viewer", "viewer"),
)
