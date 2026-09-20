"""HTTP auth-gate adapter (strangler bridge 1, AGENTS.md §3.1).

Re-exports the legacy `platform.auth` route gates for this module's controllers:
the gates stay byte-identical while the auth domain migrates, and the day they move
into `modules/auth`, only this file changes. Non-adapter module files must never
import `voiceai.platform` directly — the layer-contract test enforces that.
"""

from voiceai.platform.auth import require_role, require_scope

__all__ = ["require_role", "require_scope"]
