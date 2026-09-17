# Feature Specification: Submodule Architecture Restructure

**Feature Branch**: `001-submodule-restructure`

**Created**: 2026-09-15

**Status**: Draft

**Input**: User description: "Restructure the repo into a submodule-based architecture. Each submodule contains models, repositories, controllers, errors, exceptions, utils, static_methods, helpers, constants, services files with strict layer separation (controllers use services only; services hold business logic; repositories hold DB interaction; errors/exceptions live in their module files). Add a common module (shared response boilerplate, pagination, datetime utils), a single shared logger (otobaai-logger), a core module (environment, DI container, redis, db configuration), and a database submodule (base record class with timestamps, creation/updation user ids, is_active toggle, meta fields; central collection-name constants). Enforce declared types and type hints on all variables and methods, informative docstrings on every class and method, strict Sonar standards, dependency injection for all dependencies, and test suites for every module and case — while maintaining project security throughout."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Shared foundation is available to all modules (Priority: P1)

A backend contributor building or migrating any submodule can obtain configuration values, a logger, a database session, and shared helpers (standard responses, pagination, datetime handling) from one well-known place, instead of constructing clients or redefining shapes locally.

**Why this priority**: Everything else depends on it. Without the shared foundation (core, common, database base, single logger), submodule migrations have nothing to plug into, and per prior agreement this is built first.

**Independent Test**: Can be fully tested by writing a throwaway check that resolves each shared capability (config value, logger, DB session factory, response envelope, pagination, datetime helper) from the foundation alone, with no domain submodule present, and delivers a reusable base for all later work.

**Acceptance Scenarios**:

1. **Given** a fresh checkout, **When** a contributor looks for where configuration, logging, database access, and shared response shapes live, **Then** there is exactly one documented location for each and no competing alternatives.
2. **Given** the foundation in place, **When** a new submodule is scaffolded, **Then** it reuses the shared response shape, pagination behavior, datetime handling, and base record fields without redefining any of them.

---

### User Story 2 - Pilot submodule follows the ten-file shape with zero behavior change (Priority: P2)

A maintainer takes one existing submodule (the platform area, which already has models, routing, and storage concepts) and reorganizes it into the standard ten-file layout — models, repositories, controllers, services, errors, exceptions, utils, static_methods, helpers, constants — with request handling calling services only, business logic living in services only, and database interaction living in repositories only. External behavior (API responses, call flows) is unchanged.

**Why this priority**: It proves the pattern on real code and produces the reference example every later migration copies. It is the first visible slice of the restructure.

**Independent Test**: Can be fully tested by running the existing test suite plus new per-layer tests for the pilot module before and after migration — all green, with identical externally observable behavior — delivering the proven migration pattern.

**Acceptance Scenarios**:

1. **Given** the pilot submodule before migration, **When** it is reorganized into the ten-file layout, **Then** every request path still behaves as before and every existing test for it still passes.
2. **Given** the migrated pilot module, **When** a reviewer inspects any controller, **Then** it contains no business rules and no database access; **When** they inspect any service, **Then** it contains no direct database access; **When** they inspect any repository, **Then** it contains no business rules.

---

### User Story 3 - Remaining submodules migrate one at a time without breaking the product (Priority: P3)

Contributors migrate each remaining submodule (voice pipeline areas such as speech-to-text, language models, speech synthesis, telephony handling, agent management) into the standard layout incrementally, with the product remaining shippable after each step.

**Why this priority**: This is the bulk of the work, but each module migration is valuable on its own (clearer ownership, testable seams) and can be scheduled independently once the pattern is proven.

**Independent Test**: Can be fully tested by migrating a single submodule in isolation — old import paths keep working through compatibility shims, the full test suite stays green — delivering one fully conforming module per increment.

**Acceptance Scenarios**:

1. **Given** a migrated and an unmigrated submodule coexisting, **When** the full test suite runs, **Then** it passes and the running product behaves identically to before the increment.
2. **Given** any migrated submodule, **When** a contributor needs to find where a business rule, a database query, or an error definition lives, **Then** exactly one file is the correct answer (services, repositories, or errors/exceptions respectively).

---

### User Story 4 - Every module ships with tests, static-analysis compliance, and security review (Priority: P4)

A reviewer evaluating any migrated or new module finds a test suite covering its service methods, repository methods, and controller routes including failure and edge cases; a passing static-analysis gate; and confirmation that secrets, input validation, authorization placement, and log hygiene meet the security baseline.

**Why this priority**: Tests and gates are what prevent the new structure from decaying. This story runs across all modules rather than delivering product behavior itself, so it follows the structural work.

**Independent Test**: Can be fully tested per module by running its test suite (including deliberately failing cases), the static-analysis gate, and the security checklist — all passing — delivering sustained quality rather than one-time tidiness.

**Acceptance Scenarios**:

1. **Given** any module claimed complete, **When** its tests, static-analysis gate, and security checklist are executed, **Then** all pass with no waivers except explicitly recorded ones.
2. **Given** a change that leaks logic across layers (e.g., database access in a controller), **When** tests or gates run, **Then** the violation is caught automatically before merge.

---

### Edge Cases

- What happens when a cross-cutting concern does not fit any single submodule (e.g., a helper genuinely needed by many modules)? It belongs in the common module after review, not duplicated per module.
- What happens when a repository method seems to need a business rule (e.g., "only return active records")? The rule lives in the service; the repository exposes a neutral query the service constrains.
- What happens when external callers still import the old module paths during migration? Compatibility re-exports keep old paths working until a recorded removal milestone.
- How does the system handle a submodule with no persistence needs? It ships without repository usage but keeps the file layout so the shape stays uniform.
- What happens when a new dependency (client, SDK, connection) is introduced? It is registered in the core dependency-injection container, never constructed inside services, repositories, or controllers.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each submodule MUST be organized into exactly the ten standard files (models, repositories, controllers, services, errors, exceptions, utils, static_methods, helpers, constants).
- **FR-002**: Controllers MUST delegate to services and MUST NOT contain business logic or repository/database access.
- **FR-003**: Services MUST contain business logic and MUST NOT access the database directly; all persistence goes through the repository.
- **FR-004**: Repositories MUST contain all database interaction and MUST NOT encode business rules.
- **FR-005**: Errors and exceptions raised by a module MUST be defined in that module's own errors/exceptions files.
- **FR-006**: Constants MUST be module-level values with nothing hard-coded ad hoc elsewhere in module code.
- **FR-007**: A common module MUST provide the project-wide response boilerplate used by all APIs, a shared pagination behavior, and shared datetime utilities (extensible to other genuinely shared concerns).
- **FR-008**: All project code MUST log through the single shared logger (otobaai-logger); ad-hoc loggers and print-style output in module code are forbidden.
- **FR-009**: A core module MUST centralize environment/config loading, dependency-injection wiring, Redis configuration, and database configuration; module code MUST NOT read process environment or construct these clients directly.
- **FR-010**: A database submodule MUST provide a base record class carrying creation/update timestamps, creation/update user ids, an is_active toggle, and meta fields, which every collection/table model inherits.
- **FR-011**: A single constants file in the database submodule MUST be the only place collection/table names are defined; module models reference it.
- **FR-012**: Every variable MUST have a declared type and every method MUST carry type hints including a return type.
- **FR-013**: Every class and method MUST carry an informative docstring covering purpose, arguments, returns, and raised errors.
- **FR-014**: The static-analysis quality gate MUST pass for every module before it is considered complete.
- **FR-015**: All dependencies (logger, services, repositories, Redis, database sessions, and any future client) MUST be provided via dependency injection; direct instantiation inside services, repositories, or controllers is forbidden.
- **FR-016**: Every module MUST ship a test suite covering each service method, repository method, and controller route, including failure and edge cases.
- **FR-017**: No secrets, tokens, or credentials may live outside the environment configuration or a secrets manager.
- **FR-018**: All external input MUST be validated at the controller boundary before reaching the service layer.
- **FR-019**: Authorization checks MUST live in the service layer and MUST NOT be assumed from upstream handling.
- **FR-020**: No personal or sensitive caller data may be written to logs, and database access MUST follow least privilege.
- **FR-021**: Layering and dependency-direction rules MUST be enforced by automated checks (not reviewer vigilance alone), so violations fail before merge.
- **FR-022**: Migration MUST proceed incrementally with old import paths kept working until recorded removal milestones, keeping the product shippable after each step.

### Key Entities

- **Submodule**: A bounded area of the product (e.g., platform administration, voice pipeline stage) owning its ten standard files and its module-local errors; depends on the shared foundation, never on another submodule's internals.
- **Shared Foundation**: The core module (configuration, dependency injection, database/Redis setup), the common module (responses, pagination, datetime), and the single logger — the only cross-module providers.
- **Base Record**: The inherited record shape giving every stored collection/table creation/update timestamps, creation/update user ids, an is_active toggle, and meta fields.
- **Collection Registry**: The single list of collection/table names referenced by all module models.
- **Response Envelope**: The one standard API response shape produced via the common module.
- **Module Test Suite**: The per-module tests covering service methods, repository methods, and controller routes including failure and edge cases.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new contributor can locate where any business rule, database query, error definition, or shared helper lives on the first attempt in 9 out of 10 tries.
- **SC-002**: Every migrated module passes its full test suite (including failure/edge cases), the static-analysis gate, and the security checklist with no unrecorded waivers.
- **SC-003**: The existing end-to-end behavior is preserved: the full pre-existing test suite passes unchanged in behavior after each migration increment.
- **SC-004**: Zero configuration reads, client constructions, response-shape definitions, or collection-name definitions exist outside their single designated locations.
- **SC-005**: Time to add a new submodule (scaffolded from the standard layout with tests and gates passing) is under one working day.

## Assumptions

- Migration proceeds incrementally (strangler pattern) with compatibility re-exports; no feature freeze is assumed. (Changeable at `/speckit.clarify` if a big-bang rewrite is preferred.)
- Shared foundation (core, common, database base, single logger) is built before any domain submodule migration, per prior agreement.
- The persistence backend for the database submodule is MongoDB; the existing Redis/memory store is kept behind a repository interface during migration.
- The single logger is provided as an in-repo wrapper over the existing logger configuration, injected through the core container.
- Until a SonarQube server is provisioned, the configured linter plus a strict type checker and a security scanner constitute the "equivalent" static-analysis gate.
- Externally observable product behavior (API responses, voice-call flows) does not change as part of this restructure; it is purely structural.
- The platform area serves as the pilot migration because it already separates models, routing, and storage concepts.
