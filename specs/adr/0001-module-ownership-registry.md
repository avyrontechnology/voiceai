# ADR 0001: Module ownership lives in the registry, not CODEOWNERS

Date: 2026-09-23. Status: accepted. Spec: 0000.

## Context

100 engineers need unambiguous ownership without GitHub-team administration
overhead during the revamp phase.

## Decision

`ModuleDef` carries `owner_squad`, `slack_channel`, `runbook_path`
(defaulted so existing construction keeps compiling). `docs/OWNERSHIP.md` is
rendered from the registry. `test_registry.py` pins non-empty owner + runbook
per entry. No `CODEOWNERS` files.

## Consequences

- Fast moves, single truth next to the mount point.
- Review discipline shifts to spec file-sets + integrator (AGENTS.md §9).
- Revisit when leaving `revamp/*`: GitHub required-reviewers can layer on top
  without changing module code.
