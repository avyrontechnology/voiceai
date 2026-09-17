# Pytest Baseline (pre-migration)

**Captured**: 2026-09-15 (task T004, before any migration increment)
**Command**: `python3 -m pytest tests/ -q -p no:cacheprovider`
**Interpreter**: system python3.13 (full runtime deps; `.venv` lacks litellm/pydub)

## Result

- 1990 passed, 1 skipped, **1 failed**
- Pre-existing failure (unrelated to restructure; no runtime code touched yet):
  - `tests/test_talko_dialer.py::test_run_batch_talko_dials_entries` — AssertionError

## Rule

Every migration increment must match this baseline: same pass count or
better, and no NEW failures. The talko_dialer failure is tracked
separately and must not be hidden inside migration diffs.

## Closure (2026-09-15)

The `talko_dialer` failure is resolved: the test asserted `completed == 2`
while the implementation (by documented design) parks trunk-accepted dials
as `pending` until the Talko→voiceai CDR callback lands. The test now pins
the documented contract (`pending == 2`). Suite: **2135 passed, 0 failed**.
Follow-up: wire the CDR completion callback (pre-existing product gap).
