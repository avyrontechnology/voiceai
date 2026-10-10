# automation

Graphs: their records, their snapshot history, and the engine that validates,
dry-runs and deploys them. Migrated out of the legacy `platform` bucket in spec
0078, where the records were a `{payload: ...}` row behind a `MemoryStore` and
the engine was `voiceai/platform/graphs.py`.

## Layout

| File | Role |
|---|---|
| `constants.py` | Routes, scopes, tier ranks, defaults |
| `engine.py` | The graph engine — validate / dry-run / deploy mapping (pure) |
| `models.py` | `GraphDoc`, `GraphVersion` |
| `schemas.py` | Request/response bodies |
| `errors.py` | Graph not found, version not found, invalid definition (422) |
| `exceptions.py` | Guard helpers that raise them |
| `repository.py` | Storage, including the legacy-envelope read |
| `service.py` | Business logic + the shared definition parse |
| `static_methods.py` | Version numbering (pure) |
| `helpers.py` | Small mapping helpers |
| `utils.py` | Process-state utilities |

## Why records and engine moved together

This is the one structural rule here, and it was learned by breaking it. An
earlier attempt migrated only the records and left `/validate`, `/dry-run` and
`/deploy` on the legacy store. That left **two readers pointing at two different
stores for the same collection** — a graph created through the module was
invisible to the engine routes, and one created through the legacy store was
invisible to the module.

It looked like a clean seam and it was a silent data split. The rule:

> **A family whose endpoints call its own engine moves as one unit.**

The four families migrated before this one (numbers, knowledge, webhooks,
integrations) were flat CRUD, so storage alone was a complete move. Graphs are
not.

## Behaviours worth knowing before editing

- **Version numbering counts, it does not maximise.** `len(versions) + 1`, and
  restore URLs address snapshots by that number.
- **Restore snapshots first**, so it is reversible.
- **An invalid definition is a 422 with an opaque detail** — locations and error
  types, never the validator message (spec 0052).
- **`ValidationResult` is shared**, and lives in `common/validation.py`, not
  here: the workflow engine returns it too. It lived in `platform/graphs.py`
  until this spec, which made the workflow engine import a graph module for a
  shape that has nothing to do with graphs.
- **Reads go through `raw_documents()`**, so pre-migration `{payload: ...}` rows
  stay listable — a graph that stops listing breaks its own engine routes.
