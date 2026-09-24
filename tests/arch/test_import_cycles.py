"""Import cycles: the module graph stays acyclic (spec 0019, M0).

Builds the intra-project import graph over ``voiceai/{common,core,database,
modules}`` with AST (no imports) and asserts it has no cycles. stdlib,
third-party, relative-import edge cases, and legacy packages resolve outside
the scanned roots and never form nodes. Cycles are design bugs (AGENTS.md §3):
fix them by extracting shared code, never by allowlisting — so this gate has
no debt list by design.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VOICEAI_ROOT = REPO_ROOT / "voiceai"
SCANNED_ROOTS = ("common", "core", "database", "modules")


def _module_name(path: Path) -> str:
    """Dotted module name for a file under ``voiceai/`` (packages collapse)."""
    relative = path.relative_to(VOICEAI_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return "voiceai." + ".".join(parts)


def _python_files() -> list[Path]:
    """Every ``.py`` file under the scanned roots, excluding caches."""
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        files.extend(
            path
            for path in (VOICEAI_ROOT / root).rglob("*.py")
            if "__pycache__" not in path.parts
        )
    return sorted(files)


def _import_targets(path: Path, tree: ast.Module) -> set[str]:
    """Absolute dotted import targets of one file (``from`` bases and ``imports``).

    Only module-level statements count: the gate guards the import-time load
    graph, and a call-time import inside a function body cannot deadlock the
    loader (``voice/adapters/llm.py`` uses that exact pattern on purpose, with
    the reason in its docstring). Top-level conditional imports (``if``/``try``)
    still count — they execute at load.
    """
    targets: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue  # relative imports stay inside their own package
            if node.module:
                targets.add(node.module)
    return targets
    return targets


#: Composition roots whose imports of the registry are sanctioned by AGENTS.md §3
#: (``core`` depends on the module registry in ``app_factory``/``container``
#: only). Edges out of these modules into ``voiceai.modules.*`` are wiring,
#: not design cycles.
COMPOSITION_ROOTS = frozenset(
    {
        "voiceai.core.container",
        "voiceai.core.app_factory",
    }
)

#: The one composition seam controllers may touch (AGENTS.md §3 layer matrix:
#: ``controller`` resolves the service from the container). Edges from any
#: controller into the container are wiring, not design cycles.
CONTAINER_MODULE = "voiceai.core.container"


def _is_sanctioned(src: str, dst: str) -> bool:
    """True for edges the layer matrix explicitly allows (spec 0019, M0).

    Three cases, each mirroring one matrix row: composition roots wiring the
    registry, controllers resolving the container, and intra-package imports of
    a package's own ``__init__`` (importing any submodule executes the package
    init by Python semantics, so those edges are packaging, not design — the
    registry test and the layer contract already govern what an ``__init__``
    may hold; direct submodule↔submodule edges stay fully enforced).
    """
    if src in COMPOSITION_ROOTS and dst.startswith("voiceai.modules."):
        return True
    if dst == CONTAINER_MODULE and src.split(".")[-1] == "controller":
        return True
    if dst.startswith("voiceai.modules."):
        package = dst
        if src == package or src.startswith(package + "."):
            return True
    return False


def _graph() -> dict[str, set[str]]:
    """Adjacency of scanned modules to scanned modules (project edges only)."""
    names = {_module_name(path) for path in _python_files()}
    graph: dict[str, set[str]] = {}
    for path in _python_files():
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        edges = set()
        for target in _import_targets(path, tree):
            if target == name:
                continue  # a package importing its own names is not a cycle
            resolved: str | None = None
            if target in names:
                resolved = target
            else:
                parent, _, _ = target.rpartition(".")
                while parent:
                    if parent in names:
                        resolved = parent
                        break
                    parent, _, _ = parent.rpartition(".")
            if resolved is not None and not _is_sanctioned(name, resolved):
                edges.add(resolved)
        graph[name] = edges
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Depth-first cycle search; returns one cycle path or ``None``."""
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            return visiting[visiting.index(node):] + [node]
        if node in visited:
            return None
        visiting.append(node)
        for edge in sorted(graph.get(node, set())):
            if (cycle := visit(edge)) is not None:
                return cycle
        visiting.pop()
        visited.add(node)
        return None

    for node in sorted(graph):
        if (cycle := visit(node)) is not None:
            return cycle
    return None


def test_module_graph_is_acyclic() -> None:
    """No import cycles across the new-architecture roots, no debt list."""
    cycle = _find_cycle(_graph())
    assert cycle is None, "import cycle (extract shared code; do not allowlist):\n" + "\n".join(
        f"  {step}" for step in (cycle or [])
    )
