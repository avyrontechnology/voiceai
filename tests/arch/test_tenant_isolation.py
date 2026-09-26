"""Tenancy tripwire: no silent tenancy drift before M1 enforces it (spec 0019, M0).

Full enforcement (tenant-scoped repositories, middleware coverage, credential
resolution) is M1's job. This gate only guarantees no one builds tenancy-shaped
code in the dark: the set of source files mentioning ``tenant_id`` and the set
building ``t:``-prefixed keys must both equal their recorded allowlists, both
empty today. Any M1 file updates its set loudly in the same spec.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    REPO_ROOT / "voiceai" / "common",
    REPO_ROOT / "voiceai" / "core",
    REPO_ROOT / "voiceai" / "database",
    REPO_ROOT / "voiceai" / "modules",
)

#: Source files allowed to mention ``tenant_id``. Starts with its single
#: legitimate member (the key builder itself); M1 appends in the same specs
#: that first need tenancy — never in advance.
TENANT_ID_FLAGGED: frozenset[str] = frozenset(
    {
        "voiceai/common/keys.py",
        "voiceai/common/tenancy.py",  # spec 0020, M1a: ambient context vocabulary
        "voiceai/core/app_factory.py",  # spec 0020, M1b: tenant middleware binding
        "voiceai/core/container.py",  # spec 0020, M1b: scoped wiring (reads ambient tenant)
        "voiceai/database/base.py",  # spec 0020, M1b: the tenant_id field itself
        "voiceai/database/constants.py",  # spec 0020, M1b: TENANT_ID_FIELD literal
        "voiceai/database/scoped.py",  # spec 0020, M1b: the scoping choke point itself
        "voiceai/modules/agents/runtime/compiled.py",  # spec 0020, M1b: tenant-keyed cache
        "voiceai/modules/agents/tests/test_runtime_compiled.py",  # spec 0020, M1b: key pins
        "voiceai/modules/auth/models/invite.py",  # spec 0020, M1b: org-carried invite
        "voiceai/modules/auth/models/session.py",  # spec 0020, M1b: tenant sync validator
        "voiceai/modules/auth/models/user.py",  # spec 0020, M1b: tenant sync validator
        "voiceai/modules/auth/service.py",  # spec 0020, M1b: credential → context mapping
        "voiceai/modules/auth/tests/test_tenancy.py",  # spec 0020, M1b: boundary pins
        "voiceai/modules/chat/controller.py",  # spec 0038: tenant-scoped chat reads
        "voiceai/modules/chat/service.py",  # spec 0038: session ownership checks
        "voiceai/modules/chat/tests/test_controller.py",  # spec 0038: isolation pins
        "voiceai/modules/chat/tests/test_service.py",  # spec 0038: ownership pins
        "voiceai/modules/catalog/models.py",  # spec 0022: system default tenant
        "voiceai/modules/catalog/repository.py",  # spec 0022: system-tenant view
        "voiceai/modules/catalog/tests/test_controller.py",  # spec 0022: boot seed pins
        "voiceai/modules/catalog/tests/test_seed.py",  # spec 0022: tenant pins
        "voiceai/modules/catalog/tests/test_service.py",  # spec 0022: stamp pins
        "voiceai/modules/voice/controller.py",  # spec 0021, M2: channel tenant binding
        "voiceai/modules/voice/tests/test_controller.py",  # spec 0021, M2: gate pins
        "voiceai/modules/tools/models.py",  # spec 0029: system default tenant
        "voiceai/modules/tools/repository.py",  # spec 0029: dual-view reads
        "voiceai/modules/tools/static_methods.py",  # spec 0029: system predicate
        "voiceai/modules/tools/tests/test_seed.py",  # spec 0029: tenant pins
        "voiceai/modules/tools/tests/test_service.py",  # spec 0029: isolation pins
        "voiceai/modules/voice/tests/test_outbound_bridge.py",  # spec 0026: tenant pass-through pin
        "voiceai/modules/voice/tests/test_place_call.py",  # spec 0021, M2: dial ownership pins
        "voiceai/modules/voices/models.py",  # spec 0025: tenant-scoped voice rows
        "voiceai/modules/voices/tests/test_service.py",  # spec 0025: isolation pins
        "voiceai/modules/wallet/adapters/legacy_store.py",  # spec 0020, M1b: translation stamps
    }
)

#: Matches a hand-built tenant key: a quote followed by ``t:{``. The quote
#: boundary keeps innocent substrings (``event:{``, ``amount:{``) out.
TENANT_KEY_PATTERN = re.compile(r"""["']t:\{""")

#: Source files allowed to hand-build tenant keys (empty: the one true builder
#: is ``common.keys.tenant_key``; anything here fails loudly instead).
TENANT_KEY_FLAGGED: frozenset[str] = frozenset()

#: Source files allowed to call ``system_scope`` (spec 0020, M1b) — the explicit,
#: greppable exception for tenant *discovery* (credential → tenant resolution,
#: backfills). Starts with the definition itself; each call site is appended in
#: the same spec that introduces it, never in advance.
SYSTEM_SCOPE_FLAGGED: frozenset[str] = frozenset(
    {
        "voiceai/database/scoped.py",
        "voiceai/core/container.py",  # spec 0020, M1b: auth discovery store
    }
)


def _source_files() -> list[Path]:
    """Every ``.py`` file under the new-architecture source roots."""
    files: list[Path] = []
    for root in SOURCE_ROOTS:
        files.extend(
            path for path in root.rglob("*.py") if "__pycache__" not in path.parts
        )
    return sorted(files)


def _files_containing(needle: str) -> frozenset[str]:
    """Repo-relative paths of source files whose text contains ``needle``."""
    found: set[str] = set()
    for path in _source_files():
        if needle in path.read_text(encoding="utf-8"):
            found.add(path.relative_to(REPO_ROOT).as_posix())
    return frozenset(found)


def _files_matching(pattern: re.Pattern[str]) -> frozenset[str]:
    """Repo-relative paths of source files whose text matches ``pattern``."""
    found: set[str] = set()
    for path in _source_files():
        if pattern.search(path.read_text(encoding="utf-8")):
            found.add(path.relative_to(REPO_ROOT).as_posix())
    return frozenset(found)


def test_tenant_id_appears_only_in_flagged_files() -> None:
    """No tenancy-shaped code lands without updating the flagged set (M1 owns it)."""
    found = _files_containing("tenant_id")
    assert found == TENANT_ID_FLAGGED, (
        "tenancy footprint drift (update TENANT_ID_FLAGGED in the same spec):\n"
        + "\n".join(f"+ {path}" for path in sorted(found - TENANT_ID_FLAGGED))
        + "\n".join(
            f"- {path} (migrated — remove from the flagged set)"
            for path in sorted(TENANT_ID_FLAGGED - found)
        )
    )


def test_tenant_keys_are_built_only_by_the_one_builder() -> None:
    """Hand-rolled ``t:`` key prefixes fail; ``tenant_key()`` is the only way."""
    found = _files_matching(TENANT_KEY_PATTERN) - {"voiceai/common/keys.py"}
    assert found == TENANT_KEY_FLAGGED, (
        "hand-built tenant keys (use common.keys.tenant_key instead):\n"
        + "\n".join(sorted(found - TENANT_KEY_FLAGGED))
    )


def test_unscoped_access_goes_only_through_system_scope() -> None:
    """Unscoped repository use is an allowlisted exception, not a quiet default."""
    found = _files_containing("system_scope")
    assert found == SYSTEM_SCOPE_FLAGGED, (
        "unscoped access outside system_scope (update SYSTEM_SCOPE_FLAGGED in the same spec):\n"
        + "\n".join(f"+ {path}" for path in sorted(found - SYSTEM_SCOPE_FLAGGED))
        + "\n".join(
            f"- {path} (migrated — remove from the flagged set)"
            for path in sorted(SYSTEM_SCOPE_FLAGGED - found)
        )
    )
