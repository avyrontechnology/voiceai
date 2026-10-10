# Integrations — Runbook

Third-party provider configuration and credentials. Migrated out of the legacy
`platform` bucket in spec 0077.

**This module stores credentials.** Treat every response body as sensitive until
you have read this page.

## Endpoints

| Method | Path | Scope | What it does |
|---|---|---|---|
| POST | `/api/v1/integrations` | `platform:write` | Register an integration (201) |
| GET | `/api/v1/integrations` | `platform:read` | List, masked |
| GET | `/api/v1/integrations/{id}` | `platform:read` | One, masked |
| PUT | `/api/v1/integrations/{id}` | `platform:write` | Partial update |
| DELETE | `/api/v1/integrations/{id}` | `platform:write` | Tombstone |

## What masking looks like

```
POST {"kind":"twilio","name":"main","config":{"api_key":"sk-live-9f3a…","account_sid":"AC123"}}
201  {"config":{"api_key":"••••••••","account_sid":"AC123"}}
```

- A field is masked when its **name** contains `key`, `secret`, `token` or
  `password` (case-insensitive).
- Only **string** values are masked. A dict or number under a secret-looking name
  is returned as-is.
- `account_sid` is returned raw because `sid` is not a hint. That is unchanged
  behaviour — store secrets under a name the hints catch.

## The echo-back rule (important)

`PUT` treats an incoming value of `••••••••` as **"leave this field alone"** and
skips it.

This exists because the legacy code did not, and the ordinary client round-trip
destroyed credentials:

```
GET → {"config":{"api_key":"••••••••"}}
PUT → {"config":{"api_key":"••••••••"}}    ← echo
    → the stored key was overwritten with the mask. Gone.
```

So, in practice:

| You want to | Send |
|---|---|
| Rotate a key | the new value |
| Change an unrelated field | omit the secret field, or send the mask |
| **Clear** a secret | **omit the field** — sending the mask no longer blanks it |

That last row is the one behaviour change in this migration, and it is
intentional: not clearing a field is recoverable, a destroyed credential is not.

## Troubleshooting

| Symptom | Check |
|---|---|
| A secret comes back masked | Expected — that is the contract. The store still holds the real value. |
| PUT did not change a field | You sent `••••••••` for it. That means "unchanged" now; send a real value to change it. |
| 404 on update/delete | Deleted (soft — the row survives), or another tenant's integration. |
| A credential named `sid`/`signature` is visible in a response | Expected. Rename the config key to include `key`/`secret`/`token`/`password`. |
| Integration stopped working after a rotation | Check the provider dashboard — if it was rotated before this migration and via an echo-back PUT, the stored value may be the mask from before the fix. Re-enter it. |

## Security notes

- **Never log `config`.** The module logs ids and provider kind only.
- Registering needs `platform:write`; reading needs `platform:read`.
- Delete is **soft** (`is_active=False`): the credential row survives as the
  audit trail. It is not scrubbed.
- Rotating a credential: `PUT` the new value. Do not read-modify-write the whole
  config object — the guard makes that safe now, but sending only what changes
  is clearer in the audit trail.
