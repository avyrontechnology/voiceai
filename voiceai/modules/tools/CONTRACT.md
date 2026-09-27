# tools CONTRACT

Owner: `squad-platform`.

## Routes

- `GET /api/v1/tools` — TBD in the owning spec.

## Events

- in: TBD
- out: TBD

## Collections

- TBD (`Collections` member, `tenant_id`-scoped when tenancy lands).

## Live-link (spec 0046)

Shared tools are live-linked: editing a tool re-materializes every attached
agent in the same service call; reads keep serving the stored snapshot, so
the hot path never gains a lookup.

- Version stamp: `update_tool` bumps `tools_version` and re-materializes all
  attached agents (bounded `tool_refs` lookup, same tenant only), returning
  the propagation count.
- Refuse-delete: `delete_tool` answers 409 with the referencing agent count
  while references exist — never a silent cascade-detach.
- Deprecation gate: `deprecated=True` blocks NEW attaches (400 naming the
  tool); already-attached agents keep running and read back flagged with
  `stale_deprecated` plus version drift (read-only metadata, never trusted,
  never re-written on read).
- SSRF parity: embedded `url` / `tools_params` endpoints pass the same
  `is_safe_outbound_url` pre-flight (`_is_url_safe`, off-loop) as ref
  endpoints at attach time; failures are 400s naming the path, never the
  URL. Grandfathered rows keep running until re-saved.
- Seed shape: every seeded row is attachable — `webhook:pre_call_notify`
  carries a real endpoint (unseeded otherwise); no attachable-but-broken rows.
