# legacy-shim(spec-0004) — the event types live in voiceai.modules.voice.s2s.events (step B5).
"""Pure re-export keeping ``voiceai.s2s.events`` (task_manager's ``s2s_events`` alias,
`tests/test_s2s_task_manager.py`, `tests/test_s2s_providers.py`) resolving to the SAME
class objects the runner's ``isinstance`` dispatch uses. Deleted at cutover, never grown."""

from voiceai.modules.voice.s2s.events import *  # noqa: F401,F403
