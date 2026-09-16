# Otoba AI backend — development entry points (see AGENTS.md §6)
PY ?= .venv/bin/python
RUFF ?= .venv/bin/ruff
ARCH_DIRS = voiceai/common voiceai/core voiceai/database voiceai/modules
ARCH_TESTS = tests/arch
# Strict lint profile for the new architecture only; legacy keeps the repo-level config.
# ANN401 stays off (Any needs an inline "# why:" per AGENTS.md rule 6, not a lint war);
# D107 off (__init__ docstrings add nothing over the class docstring).
ARCH_SELECT = E,W,F,I,B,UP,S,ANN,D1
ARCH_IGNORE = ANN401,D107

.PHONY: setup check lint lint-arch type test test-all sec fmt cov

setup:
	uv venv .venv --python 3.10
	uv pip install --python .venv/bin/python -e '.[dev]'

lint:
	$(RUFF) check .

lint-arch:
	$(RUFF) check --select $(ARCH_SELECT) --ignore $(ARCH_IGNORE) $(ARCH_DIRS)

type:
	$(PY) -m mypy $(ARCH_DIRS) tests/arch

test:
	$(PY) -m pytest -q $(ARCH_TESTS)

# Legacy debt: the seed-users test imports a git-ignored scripts/ file (AGENTS.md §8).
test-all:
	$(PY) -m pytest -q --ignore=tests/test_seed_mongo_users.py

sec:
	$(PY) -m bandit -q -r $(ARCH_DIRS) --severity-level medium --confidence-level high

cov:
	$(PY) -m pytest -q $(ARCH_TESTS) --cov=voiceai/common --cov=voiceai/core \
		--cov=voiceai/database --cov=voiceai/modules \
		--cov-report=term-missing:skip-covered --cov-fail-under=85

fmt:
	$(RUFF) format $(ARCH_DIRS) $(ARCH_TESTS)

check: lint lint-arch type test
	@echo "check: all gates green"
