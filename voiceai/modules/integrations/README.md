# integrations

Third-party integrations: which providers a tenant has configured, their
credentials, and whether they are active. Migrated out of the legacy `platform`
bucket in spec 0077.

**This module holds secrets.** Read [CONTRACT.md](CONTRACT.md) before changing
anything that touches `config`; [RUNBOOK.md](RUNBOOK.md) covers operations.

## Layout

| File | Role |
|---|---|
| `constants.py` | Routes, scopes, the provider vocabulary, the secret hints |
| `models.py` | `Integration` + `masked()` |
| `schemas.py` | Request/response bodies |
| `errors.py` | `IntegrationNotFoundError` |
| `exceptions.py` | Guard helpers that raise it |
| `repository.py` | Storage, including the legacy-envelope read |
| `service.py` | Business logic; **where masking is applied** |
| `static_methods.py` | Masking + the config merge guard (pure) |
| `helpers.py` | Small mapping helpers |
| `utils.py` | Process-state utilities |

## The one thing to understand

`config` holds provider credentials. Two rules follow, and both are load-bearing:

1. **Reads mask, writes do not un-mask.** Every read path returns
   `••••••••` in place of a secret-looking string. Masking lives in the
   *service*, so a controller physically cannot receive a raw credential.
2. **A mask marker on the way in is ignored.** This is the fix for a real bug:
   the legacy code let a client read an integration, echo the response back, and
   **permanently overwrite its stored API key** with the mask. `merge_config`
   treats the marker as "leave this alone".

The end-to-end round-trip is pinned by
`test_a_read_update_write_round_trip_preserves_the_secret`.

## Behaviour worth knowing before editing

- **`PUT` is partial.** Omitted fields are untouched and `config` merges per key,
  so rotating one key does not drop the others.
- **Masking is a heuristic** over `key`/`secret`/`token`/`password`. A credential
  under `sid` is returned raw — same as before this migration. Store secrets under
  a name the hints catch.
- **Only strings are masked.** A nested dict under `token_config` is passed
  through untouched: masking will not corrupt a structure it does not understand,
  but it also will not protect it.
- **Reads go through `raw_documents()`**: rows written before this migration sit
  under a `{payload: ...}` envelope and would be invisible to a validating
  listing. A credential an operator cannot see is one they re-enter.
