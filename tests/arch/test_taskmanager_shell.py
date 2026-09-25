"""TaskManager thin-shell ratchet: the M5 decomposition stays decomposed (spec 0027-0037).

Every `TaskManager` method must be a thin delegator, a small predicate, or a
few-line dispatcher (at most three non-docstring statements) unless explicitly
allowlisted. The two permanent seams are `__init__`/`from_components`
(construction entries) and `__get_agent_object` (M3 factory seam). Any new
verbatim body fails loudly — new behavior belongs in `voice/session/*`.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_MANAGER = REPO_ROOT / "voiceai" / "agent_manager" / "task_manager.py"

#: Maximum non-docstring statements per method (predicates and dispatchers fit).
MAX_SHELL_STATEMENTS: int = 3

#: Methods allowed real bodies: construction entries + the M3 factory seam.
ALLOWLISTED_METHODS: frozenset[str] = frozenset(
    {
        "__init__",
        "from_components",
        "_TaskManager__get_agent_object",
    }
)


def _method_statements() -> dict[str, int]:
    """Map TaskManager method name to its non-docstring statement count."""
    tree = ast.parse(TASK_MANAGER.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "TaskManager":
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = item.name
                if name.startswith("__") and not name.endswith("__"):
                    name = f"_TaskManager{name}"
                body = [
                    statement
                    for statement in item.body
                    if not (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    )
                ]
                counts[name] = len(body)
    return counts


def test_task_manager_methods_are_thin_delegators() -> None:
    """No new verbatim bodies land on TaskManager (M5 stays decomposed)."""
    counts = _method_statements()
    assert counts, "TaskManager class not found — the ratchet lost its target"
    violations = {
        name: count
        for name, count in counts.items()
        if count > MAX_SHELL_STATEMENTS and name not in ALLOWLISTED_METHODS
    }
    assert not violations, (
        "TaskManager methods with real bodies (move to voice/session/*):\n"
        + "\n".join(f"- {name}: {count} statements" for name, count in sorted(violations.items()))
    )
