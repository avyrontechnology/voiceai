# wallet CONTRACT

Owner: `squad-billing` (`#squad-billing`).

## Routes (mounted under `/api/v1`)

- `GET /wallet` — singleton balance (role `admin`).
- `POST /wallet/topup` — add credits + ledger entry (role `admin`).
- `GET /wallet/ledger?limit&entry_type` — bounded ledger page (role `admin`,
  `limit` clamped to `MAX_PAGE_SIZE`).
- `GET /templates`, `GET /templates/{id}` (scope `platform:read`);
  `POST /templates/{id}/import` returns `{"agent_payload": …}` (scope
  `agents:write`). Errors are `WalletError` (404 family).

## Events

- in: none. out: none.

## Collections

- `wallets` (singleton row, `id="singleton"`), `ledger` (append-only entries),
  `agent_templates` (seed catalog, id = `template_id`; `templates.py` is seed
  source, the seeder upserts rows).
