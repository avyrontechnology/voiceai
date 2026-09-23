"""Enterprise guardrails for the new-architecture tree (spec 0001, Phase 9).

Four mechanical AST checks over ``voiceai/modules/``, ``voiceai/common/``,
``voiceai/core/`` and ``voiceai/database/`` — without importing any scanned
code:

1. No ``print()`` calls (single ``otobaai`` logger via DI instead).
2. No ad-hoc ``logging.getLogger()`` outside the logger owners.
3. No ``os.environ`` reads outside ``core/environment.py``.
4. No bare ``asyncio.create_task`` outside ``core/resilience.py``.

Checks 3 and 4 are ratchets, not absolute bans: every currently flagged
file predates the container/resilience migration and is recorded below with
its burn-down reference. A file NOT on the flagged set that gains a banned
call fails here, and a flagged file that drops its last banned call must be
removed from the set in the same commit so the residual list trends to zero.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Repository root, derived from this file's location (tests/arch/ is two levels down).
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Scopes the guards cover: the new-architecture tree only. Legacy packages
#: keep their own behavior pins until their migration spec moves them.
GUARD_SCOPES = (
    REPO_ROOT / "voiceai" / "modules",
    REPO_ROOT / "voiceai" / "common",
    REPO_ROOT / "voiceai" / "core",
    REPO_ROOT / "voiceai" / "database",
)

SOURCE_ENCODING = "utf-8"
CREATE_TASK_ATTR = "create_task"
ENVIRON_ATTRS = frozenset({"environ", "getenv"})
GETLOGGER_ATTR = "getLogger"
OS_MODULE = "os"
PRINT_BUILTIN = "print"

#: Files allowed to own ``logging.getLogger`` (constitution VI: the single
#: logger wrapper plus the legacy config it wraps).
GETLOGGER_OWNERS = frozenset(
    {
        "voiceai/common/logger.py",
        "voiceai/helpers/logger_config.py",
    }
)

#: The only ``os.environ`` reader (constitution V).
CORE_ENVIRONMENT = "voiceai/core/environment.py"

#: The only home for bare task creation once it lands (forward-compatible:
#: missing today, so every current call site is flagged below).
RESILIENCE_MODULE = "voiceai/core/resilience.py"

#: (file, line) print calls preserved verbatim for legacy parity.
#: ``legacy_graph.py:129`` is a never-called dead stub pinned by spec 0002.
PRINT_EXEMPTIONS = frozenset(
    {
        ("voiceai/modules/agents/brains/legacy_graph.py", 129),
    }
)

#: Burn-down reason shared by every flagged ``os.environ`` reader (spec 0001
#: Phase 9: provider api-key fallbacks and SIP tunables predate the
#: ``core/environment.py`` migration).
ENVIRON_RESIDUAL_REASON = "spec-0001-P9 environ burn-down"

#: Every file that still reads ``os.environ``/``os.getenv`` in code (comments and
#: docstrings do not count — the check is AST-based).
ENVIRON_FLAGGED_FILES = frozenset(
    {
        "voiceai/modules/agents/adapters/llm.py",
        "voiceai/modules/agents/brains/graph/generation.py",
        "voiceai/modules/agents/brains/graph/routing.py",
        "voiceai/modules/agents/brains/knowledgebase.py",
        "voiceai/modules/agents/brains/simple.py",
        "voiceai/modules/voice/asr/pool.py",
        "voiceai/modules/voice/asr/providers/assemblyai_transcriber.py",
        "voiceai/modules/voice/asr/providers/azure_transcriber.py",
        "voiceai/modules/voice/asr/providers/deepgram/connection.py",
        "voiceai/modules/voice/asr/providers/deepgram/transcriber.py",
        "voiceai/modules/voice/asr/providers/elevenlabs_transcriber.py",
        "voiceai/modules/voice/asr/providers/gemini_transcriber.py",
        "voiceai/modules/voice/asr/providers/gladia_transcriber.py",
        "voiceai/modules/voice/asr/providers/openai_transcriber.py",
        "voiceai/modules/voice/asr/providers/pixa_transcriber.py",
        "voiceai/modules/voice/asr/providers/sarvam_transcriber.py",
        "voiceai/modules/voice/asr/providers/smallest_transcriber.py",
        "voiceai/modules/voice/asr/providers/soniox_transcriber.py",
        "voiceai/modules/voice/io/input/telephony_providers/plivo.py",
        "voiceai/modules/voice/io/input/telephony_providers/sip_trunk.py",
        "voiceai/modules/voice/io/input/telephony_providers/vobiz.py",
        "voiceai/modules/voice/io/output/telephony.py",
        "voiceai/modules/voice/io/output/telephony_providers/freeswitch.py",
        "voiceai/modules/voice/io/output/telephony_providers/sip_trunk.py",
        "voiceai/modules/voice/session/composition.py",
        "voiceai/modules/voice/session/language/lid_gate.py",
        "voiceai/modules/voice/session/language/switcher.py",
        "voiceai/modules/voice/session/s2s_runner.py",
        "voiceai/modules/voice/session/turn/function_calls.py",
        "voiceai/modules/voice/tts/providers/azure_synthesizer.py",
        "voiceai/modules/voice/tts/providers/cartesia_synthesizer.py",
        "voiceai/modules/voice/tts/providers/deepgram_synthesizer.py",
        "voiceai/modules/voice/tts/providers/elevenlabs_synthesizer.py",
        "voiceai/modules/voice/tts/providers/kalpa_synthesizer.py",
        "voiceai/modules/voice/tts/providers/maya_synthesizer.py",
        "voiceai/modules/voice/tts/providers/openai_synthesizer.py",
        "voiceai/modules/voice/tts/providers/pixa_synthesizer.py",
        "voiceai/modules/voice/tts/providers/polly_synthesizer.py",
        "voiceai/modules/voice/tts/providers/rime_synthesizer.py",
        "voiceai/modules/voice/tts/providers/sarvam_synthesizer.py",
        "voiceai/modules/voice/tts/providers/smallest_synthesizer.py",
    }
)

#: Burn-down reason shared by every flagged bare-``create_task`` file
#: (spec 0001 Phase 9: pending ``TaskRegistry``/``safe_task`` adoption; the
#: ``core/resilience.py`` primitives do not exist yet).
CREATE_TASK_RESIDUAL_REASON = "spec-0001-P9 TaskRegistry burn-down"

#: Every file that still calls bare ``create_task`` (all voice runtimes —
#: transcriber/synthesizer loops, telephony listeners, session loops).
CREATE_TASK_FLAGGED_FILES = frozenset(
    {
        "voiceai/modules/voice/asr/pool.py",
        "voiceai/modules/voice/asr/providers/assemblyai_transcriber.py",
        "voiceai/modules/voice/asr/providers/azure_transcriber.py",
        "voiceai/modules/voice/asr/providers/deepgram/transcriber.py",
        "voiceai/modules/voice/asr/providers/elevenlabs_transcriber.py",
        "voiceai/modules/voice/asr/providers/gemini_transcriber.py",
        "voiceai/modules/voice/asr/providers/gladia_transcriber.py",
        "voiceai/modules/voice/asr/providers/google_transcriber.py",
        "voiceai/modules/voice/asr/providers/openai_transcriber.py",
        "voiceai/modules/voice/asr/providers/pixa_transcriber.py",
        "voiceai/modules/voice/asr/providers/sarvam_transcriber.py",
        "voiceai/modules/voice/asr/providers/smallest_transcriber.py",
        "voiceai/modules/voice/asr/providers/soniox_transcriber.py",
        "voiceai/modules/voice/io/input/default.py",
        "voiceai/modules/voice/io/input/telephony.py",
        "voiceai/modules/voice/io/input/telephony_providers/sip_trunk.py",
        "voiceai/modules/voice/io/output/telephony_providers/freeswitch.py",
        "voiceai/modules/voice/io/output/telephony_providers/sip_trunk.py",
        "voiceai/modules/voice/session/composition.py",
        "voiceai/modules/voice/session/events.py",
        "voiceai/modules/voice/session/health.py",
        "voiceai/modules/voice/session/language/switcher.py",
        "voiceai/modules/voice/session/s2s_runner.py",
        "voiceai/modules/voice/session/turn/generation.py",
        "voiceai/modules/voice/session/turn/history_sync.py",
        "voiceai/modules/voice/session/turn/output_loop.py",
        "voiceai/modules/voice/session/turn/transcript_listener.py",
        "voiceai/modules/voice/session/welcome.py",
        "voiceai/modules/voice/tts/pool.py",
        "voiceai/modules/voice/tts/providers/elevenlabs_synthesizer.py",
        "voiceai/modules/voice/tts/providers/pixa_synthesizer.py",
        "voiceai/modules/voice/tts/stream.py",
    }
)


def _python_files() -> list[Path]:
    """Return every guarded source file, skipping bytecode caches and test trees.

    Colocated `*/tests/*` trees are pinned by their own suites, not by these
    guards: compat suites must wire legacy doubles (`MemoryStore`, `os.environ`
    setup) to prove migration behavior, which production code must never do.

    Returns:
        Sorted list of ``*.py`` paths under the guard scopes.

    Raises:
        AssertionError: If a guard scope directory is missing.
    """
    for scope in GUARD_SCOPES:
        assert scope.is_dir(), f"guard scope missing: {scope}"
    found: list[Path] = []
    for scope in GUARD_SCOPES:
        found.extend(
            path for path in scope.rglob("*.py") if "__pycache__" not in path.parts and "tests" not in path.parts
        )
    return sorted(found)


def _parse(path: Path) -> ast.Module:
    """Parse a source file into an AST.

    Args:
        path: Absolute path of the file to parse.

    Returns:
        The parsed module AST.

    Raises:
        AssertionError: If the file cannot be parsed (never skip silently).
    """
    try:
        return ast.parse(path.read_text(encoding=SOURCE_ENCODING), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise AssertionError(f"enterprise guard cannot parse {path}: {exc}") from exc


def _rel(path: Path) -> str:
    """Return the repo-relative POSIX path of a guarded file.

    Args:
        path: Absolute path under one of the guard scopes.

    Returns:
        Repo-relative path string with the OS separator.
    """
    return str(path.relative_to(REPO_ROOT))


def _print_calls() -> set[tuple[str, int]]:
    """Collect ``print()`` call sites as (file, line) pairs.

    Returns:
        Set of repo-relative file paths paired with 1-based line numbers.
    """
    calls: set[tuple[str, int]] = set()
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == PRINT_BUILTIN:
                calls.add((_rel(path), node.lineno))
    return calls


def _getlogger_files() -> set[str]:
    """Collect files calling ``logging.getLogger``.

    Returns:
        Set of repo-relative file paths containing a ``getLogger`` call.
    """
    files: set[str] = set()
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == GETLOGGER_ATTR:
                    files.add(_rel(path))
    return files


def _environ_files() -> set[str]:
    """Collect files reading ``os.environ``/``os.getenv`` in code.

    Returns:
        Set of repo-relative file paths with an environment read.
    """
    files: set[str] = set()
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ENVIRON_ATTRS
                and isinstance(node.value, ast.Name)
                and node.value.id == OS_MODULE
            ):
                files.add(_rel(path))
    return files


def _create_task_files() -> set[str]:
    """Collect files calling bare ``create_task`` in code.

    Returns:
        Set of repo-relative file paths with a ``create_task`` call.
    """
    files: set[str] = set()
    for path in _python_files():
        for node in ast.walk(_parse(path)):
            if not isinstance(node, ast.Call):
                continue
            func: ast.expr = node.func
            if isinstance(func, ast.Name) and func.id == CREATE_TASK_ATTR:
                files.add(_rel(path))
            elif isinstance(func, ast.Attribute) and func.attr == CREATE_TASK_ATTR:
                files.add(_rel(path))
    return files


def test_no_print_calls_in_new_arch() -> None:
    """Only the exempted legacy-parity stub may call ``print()``."""
    found: set[tuple[str, int]] = _print_calls()
    assert found == PRINT_EXEMPTIONS, (
        "print() drift:\n"
        + "\n".join(f"+ {path}:{line}" for path, line in sorted(found - PRINT_EXEMPTIONS))
        + "\n".join(f"- {path}:{line} (stale exemption — remove it)" for path, line in sorted(PRINT_EXEMPTIONS - found))
    )


def test_no_ad_hoc_getlogger() -> None:
    """Only the logger owners may call ``logging.getLogger``."""
    offenders: set[str] = _getlogger_files() - set(GETLOGGER_OWNERS)
    assert not offenders, "ad-hoc getLogger outside the logger owners:\n" + "\n".join(sorted(offenders))


def test_no_os_environ_outside_core() -> None:
    """Environment reads live only in ``core`` or the flagged burn-down set."""
    found: set[str] = _environ_files() - {CORE_ENVIRONMENT}
    assert found == ENVIRON_FLAGGED_FILES, (
        f"os.environ drift ({ENVIRON_RESIDUAL_REASON}):\n"
        + "\n".join(f"+ {path}" for path in sorted(found - ENVIRON_FLAGGED_FILES))
        + "\n".join(
            f"- {path} (migrated — remove from the flagged set)" for path in sorted(ENVIRON_FLAGGED_FILES - found)
        )
    )


def test_no_bare_create_task() -> None:
    """Bare ``create_task`` lives only in the flagged burn-down set."""
    found: set[str] = _create_task_files() - {RESILIENCE_MODULE}
    assert found == CREATE_TASK_FLAGGED_FILES, (
        f"create_task drift ({CREATE_TASK_RESIDUAL_REASON}):\n"
        + "\n".join(f"+ {path}" for path in sorted(found - CREATE_TASK_FLAGGED_FILES))
        + "\n".join(
            f"- {path} (migrated to TaskRegistry — remove from the flagged set)"
            for path in sorted(CREATE_TASK_FLAGGED_FILES - found)
        )
    )
