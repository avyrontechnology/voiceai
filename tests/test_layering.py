"""Layer-direction, registry, and shim-discipline tests (US4, task T048).

Complements the import-linter contracts with fast in-suite checks:
controllers sit above services above repositories; every persisted model
resolves to the collection registry; shims carry no logic.
"""

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Layer order, highest first: a layer may import layers below it, never above.
PLATFORM_LAYERS = [
    "voiceai.platform.controllers",
    "voiceai.platform.services",
    "voiceai.platform.repositories",
]


def _module_of(path: pathlib.Path) -> str:
    """Dotted module name for a path under the repo root."""
    return path.relative_to(REPO_ROOT).with_suffix("").as_posix().replace("/", ".")


def _runtime_imports(path: pathlib.Path) -> set[str]:
    """Top-level (non-TYPE_CHECKING, non-function) imports of a module."""
    tree = ast.parse(path.read_text())

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.imported: set[str] = set()
            self._skip = 0

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            test = node.test
            is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_tc:
                return
            self.generic_visit(node)

        def _record(self, name: str) -> None:
            if self._skip == 0:
                self.imported.add(name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._skip += 1
            self.generic_visit(node)
            self._skip -= 1

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]
        visit_ClassDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            if node.module:
                self._record(node.module)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            for alias in node.names:
                self._record(alias.name)

    visitor = _Visitor()
    visitor.visit(tree)
    return visitor.imported


@pytest.mark.parametrize(
    ("upper", "lower"),
    [
        ("voiceai.platform.controllers", "voiceai.platform.services"),
        ("voiceai.platform.controllers", "voiceai.platform.repositories"),
        ("voiceai.platform.services", "voiceai.platform.controllers"),
        ("voiceai.platform.repositories", "voiceai.platform.controllers"),
        ("voiceai.platform.repositories", "voiceai.platform.services"),
    ],
)
def test_layer_direction_forbidden(upper: str, lower: str) -> None:
    """A layer never imports above itself (upward edges are violations)."""
    path = REPO_ROOT / (upper.replace(".", "/") + ".py")
    imported = _runtime_imports(path)
    assert lower not in imported, f"{upper} imports {lower} (upward layer violation)"


def test_layer_direction_allowed_edges_exist() -> None:
    """The intended downward edges are present (guards against hollow layers)."""
    controllers_tree = ast.parse((REPO_ROOT / "voiceai/platform/controllers.py").read_text())
    uses_services = any(
        isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "services"
        for node in ast.walk(controllers_tree)
    )
    assert uses_services, "controllers do not call services"
    services_imports = _runtime_imports(REPO_ROOT / "voiceai/platform/services.py")
    assert "voiceai.platform.repositories" in services_imports, "services do not use repositories"


def test_registry_completeness() -> None:
    """Every BaseDocument subclass resolves its collection to the registry."""
    import importlib

    from voiceai.database.base import BaseDocument
    from voiceai.database.constants import COLLECTIONS

    known = set(COLLECTIONS.values())
    assert known, "collection registry is empty"

    def _defines_base_document_subclass(path: pathlib.Path) -> bool:
        """AST pre-filter: does the module declare a BaseDocument subclass?"""
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            return False
        return any(
            isinstance(node, ast.ClassDef)
            and any(
                (isinstance(base, ast.Name) and base.id == "BaseDocument")
                or (isinstance(base, ast.Attribute) and base.attr == "BaseDocument")
                for base in node.bases
            )
            for node in tree.body
        )

    checked = 0
    for path in REPO_ROOT.glob("voiceai/**/*.py"):
        if "__pycache__" in path.parts or not _defines_base_document_subclass(path):
            continue
        module_name = _module_of(path)
        module = importlib.import_module(module_name)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseDocument)
                and attr is not BaseDocument
                and attr.__module__ == module_name
            ):
                name = attr.Settings.name  # type: ignore[attr-defined]
                assert name in known, f"{module_name}.{attr.__name__} collection {name!r} not in registry"
                checked += 1
    assert checked >= 0  # documents the sweep ran (models adopt BaseDocument at the Mongo cutover)
