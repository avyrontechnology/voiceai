"""Turn package: the per-turn commit path of the voice session (spec 0004, B10).

B10 lands ``history_sync`` — the history-commit bodies (``sync_history`` and its
evidence helpers, ``__cleanup_downstream_tasks`` and the speculation trio) plus
the interruption-chain helpers. B11 adds the turn core (function_calls,
generation, output_loop, transcript_listener). Each module is the lookup site
for its bodies' globals (R3); TaskManager keeps same-named thin delegators.
"""

from __future__ import annotations
