# tools

Shareable function + webhook tool registry (spec 0029) with live-link
propagation to attached agents (spec 0046). Owned by `squad-platform`; the
binding surface is `CONTRACT.md`.

- Routes: `/api/v1/tools` CRUD, scope-gated (spec 0049) — `platform:read`
  to list/read, `platform:write` to create/update/delete. Anonymous callers
  get the 401 envelope, under-scoped principals the 403 envelope.
- Reads merge the curated system rows with the caller's tenant rows (tenant
  rows win id ties); writes are tenant-only and system rows answer 403.
- Bodies validate at the request boundary: unknown kinds or keys answer the
  422 envelope; the `internal` kind is code-review-owned (400 on API writes).
- Scope names and every other literal live in `constants.py`; the gate helper
  follows the voice-controller `_require_scope` precedent.
