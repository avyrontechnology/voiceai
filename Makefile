# Otoba AI backend — development entry points (see AGENTS.md §6)
PY ?= .venv/bin/python
RUFF ?= .venv/bin/ruff
ARCH_DIRS = voiceai/common voiceai/core voiceai/database voiceai/modules
# T1: module tests live beside their modules; `make test` runs both trees.
ARCH_TESTS = tests/arch voiceai/modules
# Strict lint profile for new-architecture SOURCES only; tests (both trees) keep the
# repo-level config — docstring/typing strictness on tests is noise (talko parity).
# ANN401 stays off (Any needs an inline "# why:" per AGENTS.md rule 6, not a lint war);
# D107 off (__init__ docstrings add nothing over the class docstring).
ARCH_SELECT = E,W,F,I,B,UP,S,ANN,D1
ARCH_IGNORE = ANN401,D107
ARCH_EXCLUDE = "*/tests/*"

.PHONY: setup check lint lint-arch type test test-all sec fmt cov docs dup cov-module test-module

setup:
	uv venv .venv --python 3.10
	uv pip install --python .venv/bin/python -e '.[dev]'

lint:
	$(RUFF) check .

lint-arch:
	$(RUFF) check --select $(ARCH_SELECT) --ignore $(ARCH_IGNORE) --extend-exclude $(ARCH_EXCLUDE) $(ARCH_DIRS)

type:
	$(PY) -m mypy $(ARCH_DIRS) tests/arch

test:
	$(PY) -m pytest -q $(ARCH_TESTS)

# Legacy debt: the seed-users test imports a git-ignored scripts/ file (AGENTS.md §8).
test-all:
	$(PY) -m pytest -q --ignore=tests/test_seed_mongo_users.py

sec:
	$(PY) -m bandit -q -r $(ARCH_DIRS) --severity-level medium --confidence-level high

# Coverage gate (spec 0004 B13a): the full suite (legacy suites pin the moved engine
# code through delegators/shims) over the new packages MINUS the leaf provider trees
# (io/asr/tts — see [tool.coverage.run] omit in pyproject.toml). Providers carry
# live-network branches no offline run can cover; their contracts are pinned by the
# dedicated offline suites (golden fixtures, characterization, provider units) that run
# green in test-all. The deselects below are exactly the documented known failures
# (AGENTS.md §8 + the suite-baseline env failure); test-all still reports them loudly.
cov:
	$(PY) -m pytest -q --ignore=tests/test_seed_mongo_users.py \
		--deselect tests/arch/common/test_constants.py::TestAppVersion::test_app_version_is_the_installed_distribution_version \
		--deselect tests/test_agent_prompts_endpoint.py::test_prompts_roundtrip \
		--deselect tests/test_agent_prompts_endpoint.py::test_prompts_missing_file_returns_null \
		--deselect tests/test_agent_prompts_endpoint.py::test_prompts_missing_agent_returns_404 \
		--deselect tests/test_prompt_resilience.py::test_missing_prompts_file_returns_empty_dict \
		--deselect tests/test_prompt_resilience.py::test_missing_prompts_result_supports_get \
		--deselect "tests/test_telephony_output_send_timeout.py::test_handle_interruption_does_not_hang_on_a_dead_socket[TwilioOutputHandler]" \
		--deselect tests/test_telephony_output_send_timeout.py::test_handle_does_not_hang_sending_audio_on_a_dead_socket \
		--cov=voiceai/common --cov=voiceai/core \
		--cov=voiceai/database --cov=voiceai/modules \
		--cov-report=term-missing:skip-covered --cov-fail-under=85

fmt:
	$(RUFF) format $(ARCH_DIRS) $(ARCH_TESTS)

docs:
	$(PY) voiceai/tooling/render_docs.py --out docs

dup:
	$(PY) voiceai/tooling/dup_blocks.py

# Per-module scope: make test MODULE=agents / make cov MODULE=wallet.
# MODULE must name a directory under voiceai/modules.
test-module:
	@test -d voiceai/modules/$(MODULE) || (echo "unknown module: $(MODULE)" && exit 1)
	$(PY) -m pytest -q voiceai/modules/$(MODULE)

cov-module:
	@test -d voiceai/modules/$(MODULE) || (echo "unknown module: $(MODULE)" && exit 1)
	$(PY) -m pytest -q voiceai/modules/$(MODULE) \
		--cov=voiceai/modules/$(MODULE) --cov-report=term-missing:skip-covered --cov-fail-under=85

check: lint lint-arch type test sec
	@echo "check: all gates green"
