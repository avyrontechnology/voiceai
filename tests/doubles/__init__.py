"""Shared test doubles for provider and task-manager tests (A11 hygiene).

This package is the single home for scripted fakes so provider tests do not
each hand-roll websockets, TaskManager stubs, or platform HTTP clients.

Contents:
  websocket.py     ScriptedWebSocket — scripted recv queue + recorded sends.
  task_manager.py  bare_tm() — MagicMock(spec=TaskManager) with .bind().
  platform.py      make_platform_client() + platform_client fixture helper.

Provider coverage map (2026-09-12):
  Migrated to doubles (behavior asserts, no source inspection):
    - assemblyai (tests/test_stt_provider_doubles.py)
    - gladia     (tests/test_stt_provider_doubles.py)
  Remaining (documented, not yet migrated):
    - azure   — covered by tests/test_transcriber_a6_fixes.py behavior guards;
                TODO: move toggle/offload checks to doubles.
    - google  — covered by tests/test_transcriber_a6_fixes.py behavior guards;
                TODO: move rotation/flush checks to doubles.
    - openai  — telephony matrix + silence-commit race in a6_fixes;
                TODO: move to doubles with ScriptedWebSocket.
    - pixa    — telephony matrix + no-phantom-silence in a6_fixes;
                TODO: move to doubles with ScriptedWebSocket.
  Synth thin spots (use ScriptedWebSocket + bare_tm):
    - cartesia, rime, deepgram, sarvam, pixa — see
      tests/test_synthesizer_a7_fixes.py; TODO: migrate sender-serialization
      checks to doubles.

Mangling rule: never write the mangled private literal in tests. Use
`tm.bind("__<name>")` (double-underscore, unmangled) or bind the public name.
"""

from tests.doubles.platform import make_platform_client
from tests.doubles.task_manager import bare_tm, private, stub, unbound_tm_attr
from tests.doubles.websocket import ScriptedWebSocket

__all__ = ["ScriptedWebSocket", "bare_tm", "make_platform_client", "private", "stub", "unbound_tm_attr"]
