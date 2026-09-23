"""AST enforcement of the AGENTS.md §3.1 strangler-bridge rules (spec 0002, step A0).

Three mechanical checks over the source tree, without importing any scanned code:

1. Under ``voiceai/modules/**`` only files inside an ``adapters/`` directory may import
   legacy ``voiceai.*`` packages (legacy = anything outside ``voiceai.common`` /
   ``voiceai.core`` / ``voiceai.database`` / ``voiceai.modules``); non-adapter files get
   exactly two transitional allowances — ``voiceai.enums`` and ``voiceai.llms`` — and a
   pinning test asserts the allowance set never silently grows.
2. Any file tagged ``# legacy-shim(`` in its first three lines must be a pure re-export:
   imports, ``__all__`` assignments, and string literals (docstring) only.
3. Files under ``voiceai/modules/voice/**`` may import from ``voiceai.modules.agents``
   only names exported through its ``__init__.__all__`` — never submodules, never the
   module object itself.

The checks are forward-compatible: packages that do not exist yet pass vacuously, so this
file lands in step A0 and stays green while A1–A7 (and spec 0004) fill the tree in.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Repository root, derived from this file's location (tests/arch/ is two levels down).
REPO_ROOT = Path(__file__).resolve().parents[2]
VOICEAI_ROOT = REPO_ROOT / "voiceai"
MODULES_ROOT = VOICEAI_ROOT / "modules"
VOICE_ROOT = MODULES_ROOT / "voice"
AGENTS_INIT = MODULES_ROOT / "agents" / "__init__.py"

PACKAGE_ROOT_NAME = "voiceai"
MODULES_PACKAGE = "voiceai.modules"
AGENTS_PACKAGE = "voiceai.modules.agents"
ADAPTERS_DIR_NAME = "adapters"
PYCACHE_DIR_NAME = "__pycache__"
PYTHON_GLOB = "*.py"
SOURCE_ENCODING = "utf-8"
SHIM_TAG = "# legacy-shim("
SHIM_TAG_SCAN_LINES = 3
DUNDER_ALL = "__all__"
STAR_IMPORT = "*"
VIOLATION_SEPARATOR = "\n"

#: The four new-architecture roots a module file may always import (AGENTS.md §3).
NEW_ARCH_PACKAGES = frozenset(
    {
        "voiceai.common",
        "voiceai.core",
        "voiceai.database",
        "voiceai.modules",
    }
)

#: §3.1's two declared transitional allowances for NON-adapter module files — and only these.
TRANSITIONAL_ALLOWANCES = frozenset({"voiceai.enums", "voiceai.llms"})


def _python_files(root: Path) -> list[Path]:
    """Return every Python source file under ``root``, skipping bytecode caches.

    Args:
        root: Directory to scan; a missing directory yields an empty list (forward
            compatibility for packages later steps create).

    Returns:
        Sorted list of ``.py`` paths.
    """
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob(PYTHON_GLOB) if PYCACHE_DIR_NAME not in path.parts)


def _parse(path: Path) -> ast.Module:
    """Parse a source file into an AST module (the file name feeds syntax errors)."""
    return ast.parse(path.read_text(encoding=SOURCE_ENCODING), filename=str(path))


def _package_parts(path: Path) -> list[str]:
    """Return the dotted-package parts a file's relative imports resolve against.

    Both ``pkg/__init__.py`` and ``pkg/module.py`` resolve level-1 imports against
    ``pkg``, which is the path with its final component dropped in either case.
    """
    parts = list(path.relative_to(REPO_ROOT).with_suffix("").parts)
    return parts[:-1]


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> tuple[str, list[str]]:
    """Resolve a ``from ... import ...`` node to its absolute base module and names.

    Relative imports are resolved against the file's package so ``from ..x import y``
    cannot smuggle a legacy import past an absolute-prefix scan.

    Args:
        path: File the node was parsed from.
        node: The import-from node.

    Returns:
        ``(base_module, imported_names)`` — ``base_module`` is absolute and dotted
        (empty only for pathological inputs), names may include ``"*"``.
    """
    if node.level == 0:
        base_parts: list[str] = []
    else:
        package = _package_parts(path)
        climb = node.level - 1
        base_parts = package[: len(package) - climb] if climb else package
    module_parts = node.module.split(".") if node.module else []
    base = ".".join(base_parts + module_parts)
    return base, [alias.name for alias in node.names]


def _import_targets(path: Path, tree: ast.Module) -> list[tuple[int, str]]:
    """Return every absolute dotted target a file imports, with line numbers.

    ``import a.b`` yields ``a.b``. ``from a.b import c`` yields ``a.b.c`` for each name
    (a child may be a module or a class — prefix classification makes the ambiguity
    harmless) and falls back to the base ``a.b`` for star imports.
    """
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base, names = _resolve_import_from(path, node)
            concrete = [name for name in names if name != STAR_IMPORT]
            if len(concrete) < len(names) and base:
                targets.append((node.lineno, base))
            for name in concrete:
                targets.append((node.lineno, f"{base}.{name}" if base else name))
    return targets


def _is_within(target: str, package: str) -> bool:
    """True when ``target`` is ``package`` itself or any dotted descendant of it."""
    return target == package or target.startswith(package + ".")


def _is_new_arch(target: str) -> bool:
    """True for imports of the four sanctioned new-architecture roots."""
    return any(_is_within(target, package) for package in NEW_ARCH_PACKAGES)


def _is_transitional(target: str) -> bool:
    """True for the two declared §3.1 transitional allowances."""
    return any(_is_within(target, package) for package in TRANSITIONAL_ALLOWANCES)


def _is_legacy(target: str) -> bool:
    """True for any ``voiceai`` import that is not a new-architecture package."""
    return _is_within(target, PACKAGE_ROOT_NAME) and not _is_new_arch(target)


def _is_adapter_file(path: Path) -> bool:
    """True when the file lives under an ``adapters/`` directory (§3.1 bridge 1)."""
    return ADAPTERS_DIR_NAME in path.parent.parts


def _first_lines(path: Path, count: int) -> list[str]:
    """Return up to ``count`` leading lines of a source file."""
    return path.read_text(encoding=SOURCE_ENCODING).splitlines()[:count]


def _is_dunder_all_stmt(node: ast.stmt) -> bool:
    """True for ``__all__ = ...`` / ``__all__ += ...`` / annotated ``__all__`` statements."""
    if isinstance(node, ast.Assign):
        return all(isinstance(target, ast.Name) and target.id == DUNDER_ALL for target in node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return isinstance(node.target, ast.Name) and node.target.id == DUNDER_ALL
    return False


def _is_string_expr(node: ast.stmt) -> bool:
    """True for a bare string-literal statement (module docstring)."""
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _agents_exported_names() -> frozenset[str]:
    """Extract ``voiceai.modules.agents.__all__`` from its ``__init__`` AST, or empty.

    Reading the AST (not importing) keeps this test collection-safe while a peer agent
    is mid-edit on the package. Only literal string elements count — anything dynamic
    is invisible to the contract and therefore not importable from ``voice``.
    """
    if not AGENTS_INIT.is_file():
        return frozenset()
    names: set[str] = set()
    for node in _parse(AGENTS_INIT).body:
        if not _is_dunder_all_stmt(node) or isinstance(node, ast.AnnAssign) and node.value is None:
            continue
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) else None
        if isinstance(value, (ast.List, ast.Tuple)):
            names.update(
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return frozenset(names)


def test_transitional_allowances_are_exactly_the_declared_two() -> None:
    """§3.1 names exactly two transitional allowances; widening the set must be loud."""
    assert TRANSITIONAL_ALLOWANCES == frozenset({"voiceai.enums", "voiceai.llms"})


def test_non_adapter_module_files_import_no_legacy() -> None:
    """Only ``adapters/`` files under ``voiceai/modules/**`` may import legacy packages."""
    violations: list[str] = []
    for path in _python_files(MODULES_ROOT):
        if _is_adapter_file(path):
            continue  # §3.1 bridge 1: adapters are the sanctioned legacy import point
        if "tests" in path.parts:
            continue  # colocated suites wire legacy doubles for compat pins; shipped code must not
        for lineno, target in _import_targets(path, _parse(path)):
            if _is_legacy(target) and not _is_transitional(target):
                relative = path.relative_to(REPO_ROOT)
                violations.append(f"{relative}:{lineno} imports legacy '{target}' outside an adapters/ directory")
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_legacy_shim_files_are_pure_reexports() -> None:
    """A ``# legacy-shim(`` tagged file may hold only imports, ``__all__``, docstring."""
    violations: list[str] = []
    for path in _python_files(VOICEAI_ROOT):
        if not any(SHIM_TAG in line for line in _first_lines(path, SHIM_TAG_SCAN_LINES)):
            continue
        for node in _parse(path).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) or _is_dunder_all_stmt(node) or _is_string_expr(node):
                continue
            relative = path.relative_to(REPO_ROOT)
            violations.append(
                f"{relative}:{node.lineno} {type(node).__name__} — a legacy shim must be a pure re-export"
            )
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_voice_imports_only_agents_public_surface() -> None:
    """``voice`` files import from agents only names in ``__all__`` (§3.1 bridge 4)."""
    voice_files = _python_files(VOICE_ROOT)
    if not voice_files:
        return  # forward-compatible: modules/voice arrives with spec 0004
    exported = _agents_exported_names()
    violations: list[str] = []
    for path in voice_files:
        relative = path.relative_to(REPO_ROOT)
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_within(alias.name, AGENTS_PACKAGE):
                        violations.append(
                            f"{relative}:{node.lineno} 'import {alias.name}' bypasses the agents __all__ surface"
                        )
            elif isinstance(node, ast.ImportFrom):
                base, names = _resolve_import_from(path, node)
                if base.startswith(AGENTS_PACKAGE + "."):
                    violations.append(f"{relative}:{node.lineno} imports agents submodule '{base}'")
                elif base == AGENTS_PACKAGE:
                    violations.extend(
                        f"{relative}:{node.lineno} '{name}' is not in voiceai.modules.agents.__all__"
                        for name in names
                        if name == STAR_IMPORT or name not in exported
                    )
                elif base == MODULES_PACKAGE:
                    violations.extend(
                        f"{relative}:{node.lineno} 'from voiceai.modules import agents' bypasses __all__"
                        for name in names
                        if name == "agents"
                    )
    assert not violations, VIOLATION_SEPARATOR.join(violations)


#: Spec 0010 debt allowlist: cross-module deep imports that predate the general
#: surface check. Each entry is (relative file, target package, imported name).
#: New code must use the target's ``__all__`` surface instead; entries are removed
#: (not added to) as the owning spec fixes them.
KNOWN_DEEP_IMPORT_DEBT: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("voiceai/modules/voice/controller.py", "voiceai.modules.auth.constants", "SESSION_COOKIE"),
        ("voiceai/modules/voice/controller.py", "voiceai.modules.auth.models.principal", "Principal"),
        ("voiceai/modules/wallet/controller.py", "voiceai.modules.auth.constants", "SESSION_COOKIE"),
        ("voiceai/modules/wallet/controller.py", "voiceai.modules.auth.models.principal", "Principal"),
    }
)

#: Web/driver packages a service file must never import (AGENTS.md §3).
SERVICE_BANNED_PREFIXES: tuple[str, ...] = ("fastapi", "starlette")

#: Project packages a ``common`` file must never import (AGENTS.md §3).
COMMON_BANNED_PREFIXES: tuple[str, ...] = ("voiceai.core", "voiceai.database", "voiceai.modules")


def _module_exported_names(package: str) -> frozenset[str]:
    """Extract ``__all__`` literal names from a module package ``__init__`` AST.

    Args:
        package: Dotted package, e.g. ``voiceai.modules.auth``.

    Returns:
        Literal string entries of ``__all__``; empty when the init is missing.
    """
    init = REPO_ROOT / Path(*package.split(".")) / "__init__.py"
    if not init.is_file():
        return frozenset()
    names: set[str] = set()
    for node in _parse(init).body:
        if not _is_dunder_all_stmt(node):
            continue
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) else None
        if isinstance(value, (ast.List, ast.Tuple)):
            names.update(
                element.value
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            )
    return frozenset(names)


def _own_module(path: Path) -> str:
    """Return the feature-module name owning a file under ``voiceai/modules/``."""
    return path.relative_to(MODULES_ROOT).parts[0]


def _is_other_module_target(target: str, own: str) -> str | None:
    """Return the other-module package when ``target`` dives into a sibling module."""
    prefix = MODULES_PACKAGE + "."
    if not target.startswith(prefix):
        return None
    rest = target[len(prefix):]
    other = rest.split(".")[0]
    if other == own or other == "tests":
        return None
    return MODULES_PACKAGE + "." + other


def test_controllers_import_no_repository_or_sibling_internals() -> None:
    """Controllers stay thin: no repositories/drivers, sibling use via ``__all__`` (spec 0010)."""
    violations: list[str] = []
    for path in _python_files(MODULES_ROOT):
        if path.name != "controller.py" or "tests" in path.parts or _is_adapter_file(path):
            continue
        own = _own_module(path)
        relative = path.relative_to(REPO_ROOT)
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if ".repository" in alias.name or ".adapters" in alias.name:
                        violations.append(f"{relative}:{node.lineno} controller imports '{alias.name}'")
                    other = _is_other_module_target(alias.name, own)
                    if other is not None:
                        violations.append(f"{relative}:{node.lineno} controller imports sibling '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                base, names = _resolve_import_from(path, node)
                if ".repository" in base or ".adapters" in base:
                    violations.append(f"{relative}:{node.lineno} controller imports '{base}'")
                    continue
                other = _is_other_module_target(base, own)
                if other is None:
                    continue
                exported = _module_exported_names(other)
                for imported in names:
                    if imported == STAR_IMPORT or imported not in exported:
                        key = (str(relative), base, imported)
                        if key not in KNOWN_DEEP_IMPORT_DEBT:
                            violations.append(
                                f"{relative}:{node.lineno} '{imported}' is not in {other}.__all__"
                            )
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_services_import_no_web_layer() -> None:
    """Services hold business logic: no FastAPI/Starlette/controller imports (spec 0010)."""
    violations: list[str] = []
    for path in _python_files(MODULES_ROOT):
        if path.name != "service.py" or "tests" in path.parts or _is_adapter_file(path):
            continue
        relative = path.relative_to(REPO_ROOT)
        for lineno, target in _import_targets(path, _parse(path)):
            if any(target == banned or target.startswith(banned + ".") for banned in SERVICE_BANNED_PREFIXES):
                violations.append(f"{relative}:{lineno} service imports web layer '{target}'")
            if ".controller" in target:
                violations.append(f"{relative}:{lineno} service imports controller '{target}'")
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_repositories_import_no_service_or_sibling_modules() -> None:
    """Repositories touch drivers: no services/controllers/sibling modules (spec 0010)."""
    violations: list[str] = []
    for path in _python_files(MODULES_ROOT):
        if path.name != "repository.py" or "tests" in path.parts or _is_adapter_file(path):
            continue
        own = _own_module(path)
        relative = path.relative_to(REPO_ROOT)
        for lineno, target in _import_targets(path, _parse(path)):
            if ".service" in target or ".controller" in target:
                violations.append(f"{relative}:{lineno} repository imports '{target}'")
                continue
            other = _is_other_module_target(target, own)
            if other is not None:
                violations.append(f"{relative}:{lineno} repository imports sibling '{target}'")
    assert not violations, VIOLATION_SEPARATOR.join(violations)


def test_common_imports_no_project_packages() -> None:
    """``common`` depends on stdlib + pydantic only (fastapi in responses.py — spec 0010)."""
    violations: list[str] = []
    for path in _python_files(VOICEAI_ROOT / "common"):
        relative = path.relative_to(REPO_ROOT)
        for lineno, target in _import_targets(path, _parse(path)):
            if target in {"fastapi", "fastapi.encoders", "fastapi.exceptions", "fastapi.responses"} or target.startswith(
                ("fastapi.",)
            ):
                if path.name != "responses.py":
                    violations.append(f"{relative}:{lineno} only responses.py may import fastapi")
                continue
            if any(target == banned or target.startswith(banned + ".") for banned in COMMON_BANNED_PREFIXES):
                violations.append(f"{relative}:{lineno} common imports project package '{target}'")
    assert not violations, VIOLATION_SEPARATOR.join(violations)
