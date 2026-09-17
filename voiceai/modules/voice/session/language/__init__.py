"""Language package: the multilingual subsystem of the voice session (spec 0004, B9a).

Region Q of ``task_manager.py`` lives here, split along the spec's target tree:

* `lid_gate` — LID evidence readers, the playback gate and the idle-flush watcher.
* `switcher` — the Switch-LLM decision path, the directive pair, the public
  ``switch_language`` and the session-bound `LanguageSwitchCoordinator`.
* `handoff` — the switch-handoff clip prewarm/render/play (owning the process-wide
  ``HANDOFF_CLIP_CACHE``).

Only the coordinator and the facade protocols are re-exported here; the moved
bodies stay addressed by their own module paths (they are the monkeypatch lookup
sites — R3), and TaskManager keeps a same-named thin delegator per moved name
until B9b ports the remaining pinning tests.
"""

from __future__ import annotations

from voiceai.modules.voice.session.language.handoff import HandoffSession
from voiceai.modules.voice.session.language.lid_gate import LidGateSession
from voiceai.modules.voice.session.language.switcher import (
    LanguageSession,
    LanguageSwitchCoordinator,
    SwitcherSession,
)

__all__ = [
    "HandoffSession",
    "LanguageSession",
    "LanguageSwitchCoordinator",
    "LidGateSession",
    "SwitcherSession",
]
