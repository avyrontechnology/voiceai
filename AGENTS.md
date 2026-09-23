# Otoba AI Backend — Agent Contract (SDD)

This is the binding contract for every human and AI iteration on this repository from the
`revamp/arch` branch onward. It replaces the old Bolna contract. Read it fully before writing
code; when a rule here conflicts with habit, the rule wins. The workflow is **spec-driven
development**: `specs/README.md` describes the loop, `specs/TEMPLATE.md` is the starting
point, and no implementation lands without a spec.

Two worlds coexist in this repo:

- **New architecture** (this contract): `voiceai/common`, `voiceai/core`, `voiceai/database`,
  `voiceai/modules/*`, tested under `tests/arch/`. All new backend/API work lands here.
- **Legacy** (realtime engine + old platform): `voiceai/agent_manager`, `transcriber/`,
  `synthesizer/`, `input_handlers/`, `output_handlers/`, `s2s/`, `llms/`, `agent_types/`,
  `helpers/`, `platform/`, `local_setup/`. Do not restructure legacy without a spec; features
  migrate out of `platform/` module-by-module (strangler pattern), never the other way.

---

## 1. The iteration loop (SDD)

1. **Spec** in `specs/NNNN-slug.md` — goal, non-goals, interface contracts, data model,
   security notes, test plan, verification, rollout.
2. **Architect pass** — a review (human or Plan agent) attacks the spec before code exists.
3. **Implement** to the spec's contracts. Multi-agent orchestration protocol: each agent owns a
   **disjoint file set** stated in its prompt; agents code only against the spec, never edit
   files they do not own, and report deviations instead of improvising; one integrator merges,
   resolves drift, and runs verification. Prefer 2–4 focused agents over one mega-task.
4. **Verify** — `make check` (lint + strict arch lint + mypy + tests) and `make sec` green.
5. **Review** the Definition of Done (§10), then commit `type(scope): summary [spec-NNNN]`.

---

## 2. The Ten Rules (project owner's standards, binding)

**Rule 1 — Submodule architecture.** Each feature is a submodule under `voiceai/modules/<name>/`
drawing from the canonical file set — `constants.py`, `models.py`, `errors.py`,
`exceptions.py`, `repository.py`, `service.py`, `controller.py`, `utils.py`, `helpers.py`,
`static_methods.py` — creating the files it needs (an empty placeholder is noise, not
compliance). Semantics:
- a. `models.py` — module-level pydantic models only; persisted models inherit
  `database.base.BaseFields`. A `models/` package may replace the file once a module
  owns more than three models (agents/auth precedent, spec 0000); the package root
  re-exports the public models and the same rule applies inside.
- b. `constants.py` — every module-level literal. **Nothing in the code is hard-coded**: any
  string/number used more than once, any route prefix, any collection name, any tunable comes
  from a constants file or the environment. (Type *annotations* are still mandatory — see
  Rule 6; this rule is about magic values, not typing.)
- c. `errors.py` holds the module's error classes (extending `common.errors.AppError`);
  `exceptions.py` holds guard/raise helpers. Raise these — never bare `Exception`/`ValueError`
  across a layer boundary.
- d. `repository.py` — the only place that touches the database/driver. Takes and returns
  module models, never raw driver documents/rows.
- e. `service.py` — all business logic. Orchestrates repositories, enforces authorization
  decisions, raises module errors. No HTTP objects in signatures.
- f. `controller.py` — thin HTTP layer: parse/validate input (pydantic), resolve the service
  from the container, call it, wrap output with `common.responses`. No business logic, no
  repository access, target ≤ 30 lines per handler.
- g. `utils.py` = module-internal impure utilities; `helpers.py` = small
  formatting/mapping helpers; `static_methods.py` = pure, deterministic, I/O-free functions.
  The same "one responsibility per file" discipline applies to any file added beyond the set.

**Rule 2 — Common module.** `voiceai/common/` owns everything project-wide: `responses.py`
(every API response is built from its envelopes — no route returns a hand-rolled dict),
`pagination.py` (the one pagination implementation), `datetime_utils.py` (no naked
`datetime.now()` anywhere — always `utc_now()`), `errors.py`/`exceptions.py`, `security.py`.
Anything needed by two modules moves to `common`, with tests, in the same change.

**Rule 3 — One logger.** The single project logger is **`otobaai`**
(`common/logger.py: get_logger("<module>")` → `otobaai.<module>`). No `print()`, no ad-hoc
`logging.getLogger` names, no per-module logging config. Log lines carry the request id;
secrets pass through `redact_secrets` before logging.

**Rule 4 — Core module.** `voiceai/core/` owns process wiring: `environment.py` (every env var
is declared, typed, and defaulted here — `os.getenv` anywhere else is a violation),
`container.py` (Dependency Injector DeclarativeContainer), `redis.py`, `db.py`, `app_factory.py`. Config flows env-file →
`Environment` → container → injected dependencies.

**Rule 5 — Database submodule.** `voiceai/database/` owns `base.py` — the `BaseFields` model
every persisted document/table inherits (`id`, `created_at`, `updated_at`, `created_by`,
`updated_by`, `is_active`, `meta`) — and `constants.py`, the single registry of
collection/table names (`Collections`). Module models import both; a literal collection name
in a repository is a violation. Deletes are soft (`is_active=False`) unless a spec argues
hard deletion.

**Rule 6 — Types everywhere.** Every variable with a non-obvious type, every function
parameter, and every return type is annotated. `mypy` must pass on the new packages AND
`tests/arch` with the exact profile pinned in `pyproject.toml [tool.mypy]` (untyped/incomplete
defs banned in source, untyped decorators banned, no implicit re-export, strict equality,
warn-on-Any-return; test defs are exempt from self-annotation but are fully checked).
`disallow_any_generics`/`disallow_untyped_calls` are deliberately off: pinned contracts use
open `dict` payloads and legacy imports are silently followed. `Any` requires an inline
`# why:` comment.

**Rule 7 — Docstrings.** Every module, class, and public method opens with an informative
Google-style docstring: one summary line, then Args/Returns/Raises where they add
information. Docstrings say *why* and *what contract*, not a restatement of the name.

**Rule 8 — Sonar standards.** Enforced subset: cognitive complexity ≤ 15 per function;
≤ 7 parameters; nesting ≤ 3; function length target ≤ 50 lines; no duplicated string literal
used 3+ times (promote to a constant); no empty `except`; no `except Exception` without
logging + re-raising or converting to an `AppError`; no commented-out code; no dead code;
`secrets` module (never `random`) for anything security-relevant; no SQL/query string
concatenation — parameterized/driver-native filters only; TODOs must carry a spec reference
(`# TODO(spec-0004): ...`).

**Rule 9 — Dependency injection.** We use `dependency_injector.containers.DeclarativeContainer` for DI. Services receive repositories/logger/clients through their constructors (wired via the container); repositories receive db/redis clients the same way. The single composition point is the `Container` in `core/container.py`. Controllers use `@inject` and `Provide` to resolve dependencies. Never manually construct dependencies in controllers, and never import a global singleton directly. Tests override container providers to inject fakes.

**Rule 10 — Tests per module.** `tests/arch/` mirrors the package tree; every module ships
unit tests (service/repository/static_methods with DI fakes) plus controller tests through the
real app factory (`httpx` ASGI transport). Offline only — no network, no live credentials.
Coverage target ≥ 85% on new packages. A behavior without a test does not exist.

---

## 3. Layer & import rules

| Layer | May import | Must not import |
|---|---|---|
| `controller` | service, models, constants, common.responses, common.errors, `core.container.get_container` (composition access only) | repository, drivers, other modules |
| `service` | repository, models, errors, constants, common.*, static_methods/helpers/utils | controllers, FastAPI types, drivers |
| `repository` | models, database.*, core.db/redis types, common.errors | services, controllers, other modules |
| `common` | stdlib + pydantic (+ fastapi in `responses.py` only) | core, database, modules, legacy |
| `core` | common, database, modules registry (app_factory/container only) | individual module internals |
| `modules/<a>` | its own files, common, database, core.container types | `modules/<b>` internals (use a service via the container), legacy code |
| legacy | (unchanged) | `voiceai.modules.*` internals |

Cycles are a design bug: if two modules need each other, extract the shared part to `common`
or a new module — spec it.

### 3.1 Strangler bridges (amendment for specs 0002/0004)

The matrix above bans modules↔legacy imports; a behavior-preserving strangler needs narrow,
audited bridges. Exactly four are sanctioned:

1. `modules/{agents,voice}/adapters/**` are the ONLY new-architecture files permitted to
   import legacy packages; every such import is tagged with the migration step that retires it.
2. A legacy file may become a pure re-export shim importing only a module's `__init__` public
   surface, tagged `# legacy-shim(spec-NNNN)` and registered on the owning spec's burn-down
   list; shims are deleted at cutover, never accreted.
3. Kwargs-injection of module objects into legacy constructors is sanctioned — receiving an
   injected object is not an import (precedent: the existing `task_manager_instance` kwarg).
4. Cross-module, `voice` imports only names exported through
   `voiceai.modules.agents.__init__.__all__`.

These rules are enforced mechanically by `tests/arch/test_layer_contract.py` (an AST walk
that runs under `make check`), not by convention.

## 4. Security standards (every iteration)

- **Boundary validation:** every request body/query is a pydantic model; never trust
  client-supplied identity fields — the acting user comes from auth context, authorization is
  decided in the service layer.
- **Error opacity:** unexpected exceptions never reach a client as text; clients get the
  envelope with `error_id`, logs get the stack. `str(exc)` in a response is a violation.
- **Secrets:** live only in `.env`/environment (never code, never git, never logs —
  `redact_secrets` before dumping any mapping). New secret env vars get `SecretStr`-style
  handling in `Environment` and a `.env.sample` entry with a placeholder.
- **Outbound calls:** any URL not hard-coded by us passes `common.security.is_safe_outbound_url`
  (SSRF: private/link-local/metadata ranges blocked); redirects are not followed blindly;
  timeouts are mandatory on every HTTP/redis/db call.
- **Injection:** no string-built queries; no `eval`/`exec`/`pickle` on external data; no
  `shell=True` with interpolated input; file paths from users are resolved and prefix-checked.
- **Crypto/randomness:** `secrets` for tokens; constant-time comparison for credentials;
  passwords only via a vetted KDF (follow `platform/auth.py`'s PBKDF2 pattern until migrated).
- **Abuse:** mutating endpoints consider rate limits and idempotency in their spec's security
  notes; pagination is always bounded (`MAX_PAGE_SIZE`).
- **Supply chain:** no new dependency without a spec note (what, why, maintenance status);
  run `make sec` (bandit) before commit; `pip-audit` when touching requirements.
- **Web boundary:** CORS is an explicit allowlist from `Environment.allowed_origins` (never
  `*` with credentials); cookies are `Secure` in prod (`cookie_secure_effective`); inbound
  correlation headers (`X-Request-ID`) are validated before they touch logs.
- **PII:** transcripts/emails/phone numbers never at INFO level; log identifiers, not payloads.

## 5. Async & performance rules (learned from this codebase's audit)

- No blocking I/O on the event loop: sync SDK calls, `requests`, file I/O, ffmpeg/CPU-heavy
  work go through `asyncio.to_thread` or a worker.
- Every long-lived loop isolates per-iteration exceptions (log with `error_id`, continue) —
  one bad item must not kill a consumer. Cancellation (`asyncio.CancelledError`) always
  propagates.
- Every `asyncio.create_task` result is retained and cancelled on shutdown.
- Unbounded growth is a bug: caches, queues, and per-call bookkeeping need a bound or an
  eviction note in the spec.

## 6. Tooling & commands

| Command | What it runs |
|---|---|
| `make setup` | `uv venv .venv --python 3.10` + `pip install -e '.[dev]'` (host Python 3.14 cannot build the pinned deps — always use the venv) |
| `make check` | ruff (repo config) + ruff strict select on new packages + mypy (new packages) + `pytest tests/arch` |
| `make test` | new-architecture tests only |
| `make test-all` | full suite (legacy included; see §8 for known master debt) |
| `make sec` | bandit (medium+/high) on the new packages |
| `make cov` | new-architecture tests with coverage, fails under 85% |
| `make fmt` | ruff format on new packages + `tests/arch` + `specs` tooling files |

CI workflows exist under `.github/workflows/` but are currently `workflow_dispatch`-only;
re-enabling them is part of the merge-to-master spec.

## 7. Git discipline

- Branches: `revamp/*` for architecture work, `feat/*`, `fix/*` off the revamp branch during
  this phase. Conventional commits with the spec id in the subject or body.
- Never commit `.env`, `data/`, recordings, or generated artifacts. Commit only when
  `make check` is green or the failure is documented in the message.

## 8. Known legacy debt (do not "fix" casually)

- On `master`, the full suite has pre-existing failures (telephony send-timeout contract,
  prompt-loader contract, prompts-endpoint auth) and `tests/test_seed_mongo_users.py` imports
  a git-ignored `scripts/` file; `make test-all` ignores that file. Real fixes already exist
  on `revamp/resilient-core` — merging that branch is its own spec, not a side effect.
- The realtime engine has documented HIGH-severity issues (see the audit artifact referenced
  in the repo history). Engine fixes follow their own specs on `revamp/resilient-core`.

## 9. For orchestrated agents (read before building)

You were launched with: a spec id, a file-ownership list, and verification commands. Rules:
own only your files; code only the spec's contracts; concurrent agents share this working
tree, so a transient import failure from a peer's half-written file is expected — retry at
the end rather than working around it; never `git add`/commit; leave scratch files out of the
repo; end with a report of changes per file, test counts, and anything you deliberately left
undone with the reason. Your report is the integrator's input — make deviations loud.

## 10. Definition of Done

- [ ] Spec exists, is current, and its contracts match the code
- [ ] Layer/import matrix respected; no cross-module internal imports
- [ ] All literals in constants/environment; no magic values
- [ ] Types + docstrings on everything public; mypy green
- [ ] Errors are `AppError` subclasses; responses use `common.responses`; nothing leaks internals
- [ ] Logger is `otobaai.*`; secrets redacted; no `print`
- [ ] DI honored end-to-end; tests inject fakes; no module-level singletons
- [ ] Tests per module, offline, green via `make check`; coverage ≥ 85% on new code
- [ ] `make sec` clean; security notes in the spec addressed
- [ ] Commit references the spec
