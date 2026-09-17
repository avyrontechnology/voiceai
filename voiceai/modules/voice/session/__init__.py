"""Session package: per-call orchestration of the voice runtime (spec 0004).

B4 lands `config.CallConfig` — Region A of ``task_manager.__init__`` as a parsed value
object. B5 adds `s2s_runner` (Region U), B6 adds `prompts` (Region E) and
`lifecycle.report` (Region V's assembly as pure builders). Later steps add composition
(B13a), welcome/dtmf/events (B8), the language subsystem (B9), turns (B10/B11) and
`lifecycle.hangup` (B7): the package grows exactly along the spec's target tree.
"""

from __future__ import annotations

from voiceai.modules.voice.session.config import CallConfig

__all__ = ["CallConfig"]
