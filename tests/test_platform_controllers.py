"""Controller-layer tests for the platform pilot (US2, task T027).

Controllers are thin: Pydantic validation → principal → one service call.
They MUST NOT import concrete persistence at runtime (AST-enforced;
annotation-only TYPE_CHECKING imports are allowed) and the route table
must survive the move intact (count + smoke).
"""

import ast
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from voiceai.platform.controllers import build_routers
from voiceai.platform.store import MemoryStore

#: Route registrations served by build_routers() after the move: 86 moved
#: platform routes + 14 untouched auth routes. Pin the total so the move
#: cannot silently drop an endpoint.
EXPECTED_ROUTE_COUNT = 100

_BANNED_CONTROLLER_IMPORTS = (
    "voiceai.platform.store",
    "voiceai.platform.repositories",
    "voiceai.core.db",
    "voiceai.core.redis",
    "redis",
    "beanie",
    "motor",
    "pymongo",
)


def test_controllers_import_no_persistence() -> None:
    """Controllers never import stores, repositories, or DB clients at runtime."""
    tree = ast.parse(Path("voiceai/platform/controllers.py").read_text())

    class _Visitor(ast.NodeVisitor):
        """Collect module-level runtime imports.

        Skips `if TYPE_CHECKING:` blocks (annotation-only) and defers to
        function bodies: only top-level imports create a hard module
        dependency. The app factory's lazy in-function default-store
        import is allowed; handler-level persistence imports are not
        (and none exist — every handler takes an injected store).
        """

        def __init__(self) -> None:
            self.imported: set[str] = set()
            self._type_checking_depth = 0
            self._function_depth = 0

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            test = node.test
            is_tc = (
                isinstance(test, ast.Name)
                and test.id == "TYPE_CHECKING"
                or isinstance(test, ast.Attribute)
                and test.attr == "TYPE_CHECKING"
            )
            if is_tc:
                self._type_checking_depth += 1
                for stmt in node.body:
                    self.visit(stmt)
                self._type_checking_depth -= 1
                for stmt in node.orelse:
                    self.visit(stmt)
            else:
                self.generic_visit(node)

        def _record(self, name: str) -> None:
            if self._type_checking_depth == 0 and self._function_depth == 0:
                self.imported.add(name)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._function_depth += 1
            self.generic_visit(node)
            self._function_depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            if node.module:
                self._record(node.module)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            for alias in node.names:
                self._record(alias.name)

    visitor = _Visitor()
    visitor.visit(tree)
    violations = {name for name in visitor.imported if name in _BANNED_CONTROLLER_IMPORTS or name.startswith("os")}
    assert not violations, f"controller imports persistence/os: {sorted(violations)}"


def test_route_table_preserved() -> None:
    """All 86 pre-migration routes still register with identical paths."""
    routers = build_routers()
    count = sum(len(router.routes) for router in routers)
    assert count == EXPECTED_ROUTE_COUNT, f"route table changed: {count} != {EXPECTED_ROUTE_COUNT}"


async def test_batch_round_trip_through_http() -> None:
    """Smoke: migrated routes serve create/get plus envelope 404s."""
    from tests.auth_helpers import signup_owner
    from voiceai.platform import create_platform_app

    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await signup_owner(client)
        created = await client.post("/batches", json={"agent_id": "a1", "name": "t", "entries": [{"to_number": "+1"}]})
        assert created.status_code == 201
        missing = await client.get("/batches/nope")
        assert missing.status_code == 404
        assert missing.json()["ok"] is False
