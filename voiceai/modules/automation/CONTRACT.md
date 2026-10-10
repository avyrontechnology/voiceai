# automation CONTRACT

Owner: `squad-agents` (`#squad-agents`).

**Records and engine move together.** That is the one structural rule here, and
it was learned by breaking it: an earlier attempt moved the records into the
module and left `/validate`, `/dry-run` and `/deploy` on the legacy store, which
left two readers pointing at two stores for the same collection. A graph created
through one route was invisible to the other, silently.

## Routes (mounted under `/api/v1`)

| Route | Engine | Scope |
|---|---|---|
| `POST /graphs` | no | `platform:write` (201) |
| `GET /graphs` | no | `platform:read` |
| `GET /graphs/{id}` | no | `platform:read` |
| `PUT /graphs/{id}` | no | `platform:write` |
| `DELETE /graphs/{id}` | no | `platform:write` |
| `GET /graphs/{id}/versions` | no | `platform:read` |
| `POST /graphs/{id}/restore/{n}` | no | `platform:write` |
| `POST /graphs/{id}/validate` | **yes** | `platform:write` |
| `POST /graphs/{id}/dry-run` | **yes** | `platform:write` |
| `POST /graphs/{id}/deploy` | **yes** | **`agents:write`** |

Exactly ten routes; the count is pinned by a test.

`deploy` is the one route that is not `platform:write` — it moves verbatim.

## Events

- in: none. out: none.

## Collections

- `platform_graphs` — `GraphDoc`. `platform_graph_versions` — `GraphVersion`.
  Row names predate this module and are kept so live graphs are not orphaned.

## Invariants

- **Version numbering is `len(existing) + 1`**, not `max + 1`. `POST /{id}/restore/{n}`
  addresses snapshots by number, so renumbering would repoint every existing
  restore URL at a different snapshot, permanently. It counts on purpose: that
  is the historical behaviour.
- **Lists are ordered by version number.**
- **Restore snapshots the live state first** (`note: before restore of v{n}`),
  then copies the target's name and definition. That is what makes a restore
  reversible.
- **An invalid stored definition is a 422**, not a 400 or a 500, and its detail
  carries dotted locations and pydantic error *types* only — never the validator
  message, which can echo input (spec 0052, error opacity).
- **Delete is soft** (`is_active=False`).
- `graph_id` / `version_id` in responses are the documents' `id`; the field names
  are legacy and stay.

## Security

- `definition` is customer content, not a credential: logged by id only, never
  dumped.
- Dry-run output includes prompt text — treat a dry-run response as customer
  content in any log that captures response bodies.

## Layering

`controller → service → repository`, with the engine as a pure module
(`engine.py`) the service and controller both call. The engine has no I/O and
no tenant knowledge, which is what lets validate/dry-run/deploy be unit-tested
without a database.

## Out of scope

Workflows and campaigns (specs 0079/0080) live on the legacy router still.
`ValidationResult` is shared with the workflow engine and lives in
`common/validation.py` — it is not a graph type.
