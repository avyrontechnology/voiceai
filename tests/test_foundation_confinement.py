"""Foundation confinement tests (US3, tasks T037–T047).

Machine-enforced versions of the CI gate: environment reads and logger
construction stay confined, and every package's exception surface stays
identity-equal to the shared errors (no forks, no shadowing).
"""

import io
import pathlib
import tokenize

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Packages whose migration is complete: zero os.environ/os.getenv reads.
ENV_CLEAN_PACKAGES = [
    "voiceai/agent_manager",
    "voiceai/agent_types",
    "voiceai/common",
    "voiceai/core",
    "voiceai/database",
    "voiceai/helpers",
    "voiceai/input_handlers",
    "voiceai/lid",
    "voiceai/llms",
    "voiceai/memory",
    "voiceai/otobaai_logger",
    "voiceai/output_handlers",
    "voiceai/platform",
    "voiceai/s2s",
    "voiceai/synthesizer",
    "voiceai/transcriber",
]

# Packages fully on the single logger (platform support files pending).
LOGGER_CLEAN_PACKAGES = [p for p in ENV_CLEAN_PACKAGES if p != "voiceai/platform"]

# Packages exposing an exceptions surface (empty __all__ documents "no
# local surface"; non-empty entries must be identity-equal re-exports).
EXCEPTION_PACKAGES = [
    "voiceai.agent_manager",
    "voiceai.agent_types",
    "voiceai.common",
    "voiceai.core",
    "voiceai.database",
    "voiceai.helpers",
    "voiceai.input_handlers",
    "voiceai.lid",
    "voiceai.llms",
    "voiceai.memory",
    "voiceai.otobaai_logger",
    "voiceai.output_handlers",
    "voiceai.platform",
    "voiceai.s2s",
    "voiceai.synthesizer",
    "voiceai.transcriber",
]


def _iter_module_files(package_dir: str) -> list[pathlib.Path]:
    """All modules in a package dir, excluding caches and shims."""
    root = REPO_ROOT / package_dir
    return [
        path for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts and path.name != "environment.py"
    ]


def _code_without_comments_and_docstrings(path: pathlib.Path) -> str:
    """Source with comments and docstrings stripped (prose may name things)."""
    tokens = tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
    kept = [
        tok.string
        for tok in tokens
        if tok.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE)
    ]
    return " ".join(kept)


@pytest.mark.parametrize("package_dir", ENV_CLEAN_PACKAGES)
def test_no_direct_env_reads_in_migrated_packages(package_dir: str) -> None:
    """Migrated packages never read os.environ (core/environment.py only)."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _iter_module_files(package_dir)
        if "os.environ" in (code := _code_without_comments_and_docstrings(path)) or "os.getenv" in code
    ]
    assert not offenders, f"direct env reads outside core/environment.py: {offenders}"


@pytest.mark.parametrize("package_dir", LOGGER_CLEAN_PACKAGES)
def test_single_logger_in_migrated_packages(package_dir: str) -> None:
    """Migrated packages log only via otobaai-logger (no ad-hoc module loggers).

    ``logging.getLogger("ThirdParty")`` level-tuning is allowed; module
    loggers (``__name__``) and legacy ``configure_logger`` are not.
    """
    offenders = []
    for path in _iter_module_files(package_dir):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if path.name == "logger_config.py" or rel == "voiceai/otobaai_logger/__init__.py":
            continue  # the wrapped legacy definition site and the wrapper itself
        code = _code_without_comments_and_docstrings(path)
        if "configure_logger" in code or "getLogger(__name__)" in code or "getLogger( __name__ )" in code:
            offenders.append(rel)
    assert not offenders, f"ad-hoc loggers outside otobaai-logger: {offenders}"


@pytest.mark.parametrize("package", EXCEPTION_PACKAGES)
def test_exception_surface_is_identity_equal(package: str) -> None:
    """Package exceptions modules re-export (never fork) shared errors."""
    import importlib

    import fastapi
    import voiceai.errors

    module = importlib.import_module(f"{package}.exceptions")
    assert isinstance(module.__all__, list), f"{package}.exceptions.__all__ must be a list"
    for name in module.__all__:
        local = getattr(module, name)
        if name == "HTTPException":
            assert local is fastapi.HTTPException
        else:
            assert local is getattr(voiceai.errors, name), f"{package}.exceptions.{name} forks voiceai.errors.{name}"
