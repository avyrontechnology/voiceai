"""Channel-credential tripwire: tickets live in exactly two homes (spec 0021, M2).

Single-use WS tickets are minted by auth and redeemed by the voice channel —
no third home. Any file outside this set that starts mentioning ``ticket``
fails loudly: channel credentials must not sprout new issuers or verifiers in
the dark. Legacy trees are out of scope (cutover-owned); only the new-arch
roots are scanned, like the M0 tenancy tripwire.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = (
    REPO_ROOT / "voiceai" / "common",
    REPO_ROOT / "voiceai" / "core",
    REPO_ROOT / "voiceai" / "database",
    REPO_ROOT / "voiceai" / "modules",
)

#: Source files allowed to mention ``ticket``. The issuer (auth) and the one
#: verifier (voice channel) plus their tests; anything else updates this set
#: loudly in the same spec — never in advance.
TICKET_TOUCHED: frozenset[str] = frozenset(
    {
        "voiceai/modules/auth/constants.py",
        "voiceai/modules/auth/controller.py",
        "voiceai/modules/auth/models/session.py",
        "voiceai/modules/auth/schemas.py",
        "voiceai/modules/auth/service.py",
        "voiceai/modules/auth/static_methods.py",
        "voiceai/modules/auth/tests/test_burndown.py",
        "voiceai/modules/auth/tests/test_controller.py",
        "voiceai/modules/auth/tests/test_module_def.py",
        "voiceai/modules/auth/tests/test_service.py",
        "voiceai/modules/voice/constants.py",
        "voiceai/modules/voice/controller.py",
        "voiceai/modules/voice/tests/test_controller.py",
    }
)


def test_tickets_live_only_in_the_two_homes() -> None:
    """No new ticket issuer or verifier lands without updating the set."""
    found: set[str] = set()
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if "ticket" in path.read_text(encoding="utf-8"):
                found.add(path.relative_to(REPO_ROOT).as_posix())
    assert found == TICKET_TOUCHED, (
        "ticket footprint drift (update TICKET_TOUCHED in the same spec):\n"
        + "\n".join(f"+ {path}" for path in sorted(found - TICKET_TOUCHED))
        + "\n".join(
            f"- {path} (migrated — remove from the set)" for path in sorted(TICKET_TOUCHED - found)
        )
    )
