# Import Shim Registry (strangler migration tracker)

**Feature**: `001-submodule-restructure`

Retired shims stay listed below as a removal record. CI (`gate.yml` "No
new shim-path imports") fails closed on any reintroduction.

| Old path | Canonical path | Removal version | Status |
|----------|----------------|-----------------|--------|
| `voiceai.platform.router` (all names) | `voiceai.platform.controllers` | v0.11.0 | **removed** 2026-09-15 (v0.11.0); zero in-repo consumers at removal |
| `voiceai.helpers.resilience` (all names) | `voiceai.core.resilience` | v0.11.0 | **removed** 2026-09-15 (v0.11.0); 19 internal callers re-pointed first |

| Old path | Canonical path | Removal version | Status |
|----------|----------------|-----------------|--------|
| `voiceai.platform.router` (all names) | `voiceai.platform.controllers` | v0.11.0 | active (US2 pilot) |
| `voiceai.helpers.resilience` (all names) | `voiceai.core.resilience` | v0.11.0 | active (US3/T047) |
| `voiceai.platform.store` (`MemoryStore`, `RedisStore`) | `voiceai.platform.repositories` | v0.12.0 | active (Phase 7/T059) |
| `voiceai.platform.mongo_store` (`MongoStore`, `COLLECTION_KEY_BY_MODEL`) | `voiceai.platform.repositories` | v0.12.0 | active (Phase 7/T059) |
| `voiceai.platform.services` (module → package, same path) | `voiceai.platform.services.<domain>` | — | converted 2026-09-16, path preserved, no shim needed (Phase 7/T056) |
| `voiceai.platform.repositories` (module → package, same path) | `voiceai.platform.repositories.<backend>` | — | converted 2026-09-16, path preserved, no shim needed (Phase 7/T057) |

## Old-path import census

Zero-tolerance (CI-enforced, `gate.yml` "No new shim-path imports"): no
`.py` file outside the two shims above may import the old paths. Census
taken 2026-09-15: **0 remaining** (internal callers re-pointed in US3;
`local_setup/quickstart_server.py` and `tests/test_resilience.py`
re-pointed in US4).

## Rules

- One module per PR; full suite green before each removal milestone.
- A shim is removed only when its old-path import count is zero and the
  removal version has shipped.
