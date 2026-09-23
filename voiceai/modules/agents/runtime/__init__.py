"""The agents runtime seam (spec 0012: enterprise performance).

New (non-verbatim) code lives here — never in ``brains/`` (frozen legacy moves):

- ``factory``: registry resolving ``agent_type`` to brain constructors.
- ``compiled``: read-through cache over the definition + prompt ports.
- ``judgments``: concurrent completion/voicemail judgment runner.
"""

from __future__ import annotations

from voiceai.modules.agents.runtime.compiled import CachedAgentReader
from voiceai.modules.agents.runtime.factory import BrainFactory, BrainPort
from voiceai.modules.agents.runtime.judgments import run_judgments

__all__ = ["BrainFactory", "BrainPort", "CachedAgentReader", "run_judgments"]
