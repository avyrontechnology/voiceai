# Contract: Voice Module Boundaries + File Budget (internal)

**Feature**: `001-submodule-restructure` | **Date**: 2026-09-16

Extends `layer-contracts.md` (L-01–L-05) for the `voiceai/voice/`
consolidation and the 1500-line file budget. Violations fail the
import-lint / file-budget gates; they are not style suggestions.

## V-01 Voice parent ownership

- All voice-pipeline code lives under `voiceai/voice/`: `pipeline/`,
  `stt/`, `tts/`, `llm/`, `s2s/`, `lid/`, `io/`, `agents/`, `memory/`
  (see `../data-model.md` Entity 6 for the source map).
- `platform/`, `core/`, `common/`, `database/`, `otobaai_logger/` MUST
  NOT move under `voice/`. New voice code MUST NOT be added outside
  `voice/` (enforced by the forbidden-import contract + code review).

## V-02 No sideways imports inside voice/

- A `voice/<sub>/` subpackage MUST NOT import another `voice/<other>/`
  subpackage's internals (`services`, `repositories`, provider
  implementations). Cross-concern calls go through the callee's service
  interface resolved via `core/container.py` (L-05), never by direct
  module import.
- `voice/*` MAY import `core/`, `common/`, `database/`,
  `otobaai_logger/` only.

## V-03 Ten-file shape per voice subpackage

- Each `voice/<sub>/` keeps exactly the ten standard files (models,
  repositories, controllers, services, errors, exceptions, utils,
  static_methods, helpers, constants) per constitution Principle I.
- Provider files (`voice/stt/<provider>.py`, `voice/tts/<provider>.py`,
  `voice/llm/<provider>.py`) are services-layer implementations behind
  the subpackage's `services.py` + pool; they MUST NOT be imported by
  controllers or repositories directly.

## V-04 File-size budget (1500 lines)

- NO `.py` file under `voiceai/` may exceed 1500 lines (`wc -l`).
- Splits MUST be by concern (mixin/facade, per-domain service, or
  per-collection repository — see `research.md` R-06), never by
  arbitrary line slicing and never by moving logic across layers to
  "save lines" (L-01–L-03 still apply to every split output).
- The five baseline violators carry recorded splits that must land
  before their shim-removal milestones: `task_manager.py` →
  `voice/pipeline/*` (7 files), `platform/services.py` → per-domain
  services, `store.py`/`mongo_store.py` → per-collection repositories,
  `graph_agent.py` → `voice/agents/graph/*`.

## V-05 Migration shims for voice moves

- Every moved path keeps a lazy PEP-562 shim at the old location
  (`voiceai.synthesizer.*`, `voiceai.transcriber.*`, `voiceai.llms.*`,
  `voiceai.s2s.*`, `voiceai.lid.*`, `voiceai.input_handlers.*`,
  `voiceai.output_handlers.*`, `voiceai.agent_manager.*`,
  `voiceai.agent_types.*`) emitting `DeprecationWarning` with a removal
  version, per `research.md` R-03. Shims contain no logic, routers, or
  models. The central shim registry tracks old→new + removal version;
  CI trends old-path import counts to zero.
