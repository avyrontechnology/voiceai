# PR checklist (spec-driven development, AGENTS.md §1/§10)

Spec: `specs/NNNN-slug.md` — <!-- link, required -->

## File set

Owned files (disjoint per agent — list them):

- …

Deviations from the spec (loud, with reason — or "none"):

- …

## Verification

- [ ] `make check` green (lint + strict arch lint + mypy + tests + bandit)
- [ ] `make sec` green
- [ ] New behavior has tests (service/repository/static_methods with DI fakes + controller via app factory, offline only)
- [ ] Coverage ≥ 85% on touched modules (`make cov` for larger changes)
- [ ] Errors are `AppError` subclasses; responses use `common.responses`; no `str(exc)` to clients
- [ ] Logger is `otobaai.*`; secrets redacted; no `print`
- [ ] No magic values (constants/environment); types + docstrings; mypy green

## Rollout

Flags / migrations / rollback: <!-- or "N/A — state why" -->
