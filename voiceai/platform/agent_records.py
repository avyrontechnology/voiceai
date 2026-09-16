# legacy-shim(spec-0002) — the agent-record helpers live in voiceai.modules.agents.static_methods (step A3).
"""Legacy import surface for the agent-record scan helpers.

Kept so ``local_setup/quickstart_server.py`` and ``tests/test_platform_agent_records.py``
import from the old path unchanged. Deleted at cutover — see the burn-down list in
specs/0002-agents-module.md §Rollout.
"""

from voiceai.modules.agents.static_methods import (
    collect_agent_records,
    is_agent_key,
    parse_agent_record,
)

__all__ = ["collect_agent_records", "is_agent_key", "parse_agent_record"]
