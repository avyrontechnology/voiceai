# voiceai — working agreement

Read `AGENTS.md` first: it is the binding contract (submodule architecture, the Ten Rules,
security standards, Definition of Done). Development is spec-driven: start every task from
`specs/README.md`, and do not write code without a spec.

Quick facts:
- New work lands in `voiceai/{common,core,database,modules}` with tests in `tests/arch/`.
- Verify with `make check` and `make sec` (use `.venv` — host Python 3.14 cannot install the
  pinned deps; `make setup` builds the right venv via uv).
- One logger: `common.logger.get_logger(...)` (`otobaai.*`). One response shape:
  `common.responses`. One pagination: `common.pagination`. Env only via `core.environment`.
- Legacy engine and `platform/` are migration sources, not places for new features.
