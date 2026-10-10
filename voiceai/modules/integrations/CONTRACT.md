# integrations CONTRACT

Owner: `squad-platform` (`#squad-platform`).

**This module holds credentials.** Unlike every other migrated family, its
security contract is the point of the code. Read this before touching `config`.

## Routes (mounted under `/api/v1`)

- `POST /integrations` — register, 201 (scope `platform:write`).
- `GET /integrations` — list, masked (scope `platform:read`).
- `GET /integrations/{id}` — one, masked (scope `platform:read`).
- `PUT /integrations/{id}` — partial update (scope `platform:write`).
- `DELETE /integrations/{id}` — tombstone (scope `platform:write`).

Errors are `IntegrationNotFoundError` (404).

## Events

- in: none. out: none.

## Collections

- `platform_integrations` (`Collections.PLATFORM_INTEGRATIONS`) — `Integration`
  documents. The row name predates this module and is kept so live **credentials**
  are not orphaned (spec 0077).

## Invariants

- **Every read returns masked config.** Any field whose name contains
  `key`/`secret`/`token`/`password` (case-insensitive) has its **string** value
  replaced with `••••••••`. Non-string values pass through untouched, so a nested
  dict or numeric setting is not mangled.
- **Only the store holds raw values.** The service returns masked copies, so a
  controller cannot leak a credential it never receives.
- **A mask marker never overwrites a stored secret.** An incoming config value
  equal to `••••••••` means "leave this field alone" and is skipped.
- **`PUT` is partial.** Omitted fields are untouched; `config` merges per key.
- **Delete is soft** (`is_active=False`) — the credential row survives as the
  audit trail.
- **`integration_id` in the response is the document `id`** — the field name is
  legacy and stays for client compatibility.

## The round-trip bug this module fixes

The legacy read path masked secrets but the write path did not un-mask them, so
the ordinary client round-trip destroyed credentials:

```
GET  →  {"config": {"api_key": "••••••••"}}
PUT  →  {"config": {"api_key": "••••••••"}}      # the client echoes what it read
store → {**stored.config, **payload.config}     # → "••••••••" overwrites the real key
```

Under the legacy code, reading an integration and PUTting the response back
**permanently destroyed the stored API key**. `merge_config` skips the marker, so
the echo-back is a no-op and a genuinely new key still merges.

This is a deliberate behaviour change, stated here rather than applied silently:
**PUTting the masked value to blank a field no longer blanks it.** Anyone relying
on that to clear a credential must send the field omitted, or send a real value.

## Security

- `config` is **never logged** — only ids and the provider `kind`. A config dump
  is a credential dump (AGENTS.md §4).
- Masking is a **heuristic**, carried over verbatim: a credential stored under a
  name with none of the hints (`sid`, `signature`) is returned raw. That is
  today's behaviour; narrowing it silently would break clients that read their own
  config back. Widening it is a separate, reviewed change.
- A secret-looking field holding a non-string is returned as-is. Masking only
  understands strings, so it cannot corrupt a structure — but it also means a
  dict-valued secret is not protected. Store secrets as strings.

## Layering

`controller → service → repository`. Masking is applied in the **service**, so it
cannot be forgotten at a call site; the schema layer only maps what it is given.

## Out of scope

Which credentials are valid, and calling the provider. This module stores
configuration; the agents/voice modules consume it.
