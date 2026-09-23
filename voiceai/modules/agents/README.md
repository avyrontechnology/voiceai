# agents

Agent definition domain: authoring schema, CRUD, prompt store, and the runtime
brain hierarchy. Storage envelopes inherit `database.base.BaseFields`.

Owned by `squad-agents`. See `CONTRACT.md` for routes/collections and
`RUNBOOK.md` for ops. `models/` is a package (not a file) because the module
owns more than three models — the package root re-exports the public models.
