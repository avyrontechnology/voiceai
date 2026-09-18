"""Every string patch target must resolve (spec 0004, B14 closeout).

The strangler moved dozens of lookup sites across B3–B13; each step repointed the
same-commit patches (R3: rewrites only where the LOOKUP SITE moves). This test is the
B14 backstop the risk register promises: it statically collects every
``patch("voiceai....")`` / ``monkeypatch.setattr("voiceai....")`` string in the suite
and proves each dotted path still resolves — module imports plus attribute chain. A
dead namespace (a shim that dropped the name, a lookup site that moved without its
repoint) fails the build here instead of silently no-opping a mock in CI.

Out of scope by construction: ``patch.object(...)`` (its object resolves at runtime),
f-string patch targets (dynamic), and non-``voiceai`` paths (stdlib/third-party).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

#: Repository root, derived from this file's location (tests/arch/ is two levels down).
REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"

_PATCH_FUNCTIONS = frozenset({"patch"})
_MONKEYPATCH_METHODS = frozenset({"setattr", "delattr"})
_MONKEYPATCH_RECEIVER = "monkeypatch"
_TARGET_PACKAGE = "voiceai"


def _python_files(root: Path) -> list[Path]:
    """Return every test source file under ``root``, skipping bytecode caches."""
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _string_patch_targets(tree: ast.Module) -> list[tuple[int, str]]:
    """Collect (lineno, dotted-path) string patch targets from one test module."""
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        dotted = first.value
        if not dotted.startswith(_TARGET_PACKAGE + "."):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _PATCH_FUNCTIONS:
            targets.append((node.lineno, dotted))
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _MONKEYPATCH_METHODS
            and isinstance(func.value, ast.Name)
            and func.value.id == _MONKEYPATCH_RECEIVER
        ):
            targets.append((node.lineno, dotted))
    return targets


def _resolve(dotted: str) -> str | None:
    """Resolve a dotted path; return ``None`` when it resolves, else the failure reason."""
    parts = dotted.split(".")
    module = None
    for width in range(len(parts), 1, -1):
        try:
            module = importlib.import_module(".".join(parts[:width]))
        except ImportError:
            continue
        break
    if module is None:
        return f"no importable module prefix in {dotted!r}"
    current: object = module
    for attribute in parts[width:]:
        if not hasattr(current, attribute):
            return f"{type(current).__name__} has no attribute {attribute!r} in {dotted!r}"
        current = getattr(current, attribute)
    return None


def test_every_string_patch_target_resolves() -> None:
    """No string patch may target a retired (moved-away) namespace."""
    violations: list[str] = []
    checked = 0
    for path in _python_files(TESTS_ROOT):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(f"{path}:{exc.lineno} unparseable: {exc}")
            continue
        for lineno, dotted in _string_patch_targets(tree):
            checked += 1
            reason = _resolve(dotted)
            if reason is not None:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno} dead patch target: {reason}")
    assert checked > 0, "no string patch targets found — the collector is broken"
    assert not violations, "\n".join(violations)
