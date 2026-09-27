"""Drift pins for the validated dynamic agent-config namespace (spec 0043, Slice E).

Spec 0043 adds one reserved free-form subtree — ``task_config.extensions`` on
``ConversationConfig`` — while everything outside it stays strict. This gate pins
that shape so later work cannot silently widen it:

1. ``extensions`` is the ONLY free-form (``dict[str, Any]``) subtree inside the
   validated agent schema (``ALLOWED_PASSTHROUGHS``; any second passthrough fails).
2. No catalog/audit read descends into ``extensions``: the walk carries exactly one
   named exemption (skip by key via ``EXTENSIONS_KEY``), the catalog tree never
   names the namespace, and no other source file under ``voiceai/`` references it.
3. Every other ``ConversationConfig`` field keeps its spec 0042 Slice D
   consumer-or-absent pin (this file mirrors ``tests/arch/test_settings_truth.py``
   without editing it; ``extensions`` is engine-ignored so it stays out of that map).
4. Both API documents describe the namespace with constant NAMES, never numbers.

Every check is textual (AST over source text plus substring/regex scans) and imports
nothing under ``voiceai/`` — the gate stays green while peer slices land in any
order and never trips on a half-written module.

INTEGRATOR NOTE (spec 0043): ``test_settings_truth.py`` compares the schema against
``FIELD_CONSUMERS`` exactly, so its exact-equality test fails once Slice A adds
``extensions`` (an engine-ignored namespace with no consumer by design). That file
is owned by no 0043 slice; close the drift there with an explicit ``extensions``
exemption rather than a consumer entry.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ENCODING = "utf-8"
AGENTS_REL = "voiceai/modules/agents"
AGENT_MODEL_REL = AGENTS_REL + "/models/agent.py"
AGENTS_CONSTANTS_REL = AGENTS_REL + "/constants.py"
STATIC_METHODS_REL = AGENTS_REL + "/static_methods.py"
SERVICE_REL = AGENTS_REL + "/service.py"
SCHEMAS_REL = AGENTS_REL + "/schemas.py"
SETTINGS_TRUTH_REL = "tests/arch/test_settings_truth.py"
OPENAPI_REL = "openapi.yaml"
API_REFERENCE_REL = "API_REFERENCE.md"
CONVERSATION_CONFIG = "ConversationConfig"
EXTENSIONS_FIELD = "extensions"
FIELD_CONSUMERS_NAME = "FIELD_CONSUMERS"
REMOVED_FIELDS_NAME = "REMOVED_FIELDS"
SCHEMA_BLOCK_MARKER = "    ConversationConfig:\n"
SCHEMA_BLOCK_END = re.compile(r"\n    \S")
OPENAPI_PROPERTY_PREFIX = "        extensions:"
OPENAPI_PROPERTY_END = re.compile(r"\n        \S")
DOCS_SECTION_MARKER = "### ConversationConfig\n"
GUIDE_MARKER = "#### Dynamic extension namespace"
PASSTHROUGH_ANNOTATION = re.compile(r"(dict|Dict)\[str,\s*Any\]")
SKIP_BY_KEY_PATTERN = re.compile(r"==\s*_?EXTENSIONS_KEY")
RAW_BOUND_PATTERN = re.compile(r"\b(32|4096|32768)\b")
DEF_PATTERN = re.compile(r"^def (\w+)\(")

#: The sole free-form subtree in the validated agent schema: dotted path → owning file.
ALLOWED_PASSTHROUGHS: dict[str, str] = {
    CONVERSATION_CONFIG + "." + EXTENSIONS_FIELD: AGENT_MODEL_REL,
}

#: Every bound/syntax literal Slice A must name (spec 0043 interface contract).
EXTENSION_CONSTANT_NAMES: tuple[str, ...] = (
    "EXTENSIONS_KEY",
    "EXTENSION_KEY_PATTERN",
    "MAX_EXTENSION_KEYS",
    "MAX_EXTENSION_VALUE_BYTES",
    "MAX_EXTENSIONS_TOTAL_BYTES",
    "EXTENSION_MAX_DEPTH",
)

#: Files allowed to reference the namespace: the five Slice A–D sources plus the
#: four Slice A–D test modules. One-way pin (extra files fail; absent entries are
#: fine) so slices land in any order. Engine, catalog, and legacy trees stay out —
#: the engine ignores these keys and the catalog never sees tenant key names.
EXTENSIONS_MENTION_ALLOWLIST: dict[str, str] = {
    AGENTS_CONSTANTS_REL: "bound/pattern constants (Slice A)",
    AGENT_MODEL_REL: "sole passthrough field + shape validator (Slice A)",
    STATIC_METHODS_REL: "merge + single audit skip (Slice B)",
    SERVICE_REL: "extensions-rooted failure mapping (Slice C)",
    SCHEMAS_REL: "clear_extensions wire field (Slice D)",
    AGENTS_REL + "/tests/test_extensions_schema.py": "schema units (Slice A)",
    AGENTS_REL + "/tests/test_extensions_merge.py": "merge/audit units (Slice B)",
    AGENTS_REL + "/tests/test_extensions_service.py": "service units (Slice C)",
    AGENTS_REL + "/tests/test_extensions_controller.py": "controller units (Slice D)",
}

#: Code shapes that reference the namespace (attribute, literal, constant, wire
#: field, annotation, dotted path). Deliberately NOT a bare ``extensions`` word
#: match (English prose such as "its extensions" must never trip this gate) and
#: NOT singular ``_extension_*`` identifiers (the unrelated extensibility family:
#: ``_extension_port``, ``playout_extension_*`` predate this spec).
NAMESPACE_PARTS: tuple[str, ...] = (
    r"EXTENSIONS_KEY",
    r"EXTENSION_KEY_PATTERN",
    r"EXTENSION_MAX_DEPTH",
    r"MAX_EXTENSION_KEYS",
    r"MAX_EXTENSION_VALUE_BYTES",
    r"MAX_EXTENSIONS_TOTAL_BYTES",
    r"clear_extensions",
    r"task_config\.extensions",
    r"[\"']extensions[\"']",
    r"\.extensions\b",
    r"\bextensions:",
)
NAMESPACE_PATTERN = re.compile("|".join(NAMESPACE_PARTS))

#: Top-level function prefixes that perform catalog/audit reads. Merge helpers
#: (``apply_agent_patch``, ``_drop_extension_key`` …) legitimately touch the
#: namespace and are excluded by construction.
AUDIT_FUNCTION_PREFIXES: tuple[str, ...] = ("audit", "_audit", "_check", "_match", "_catalog")

#: Substrings (matched case-insensitively) that mark a line as part of the one
#: named exemption rather than a value read: skip-by-key code or its rationale.
EXEMPTION_LINE_HINTS: tuple[str, ...] = (
    "extensions_key",
    "spec 0043",
    "spec-0043",
    "skip",
    "exemption",
    "descend",
)

#: Constant names the openapi extensions region must cite (bounds by name, never numbers).
OPENAPI_REQUIRED_NAMES: tuple[str, ...] = (
    "EXTENSION_KEY_PATTERN",
    "MAX_EXTENSION_KEYS",
    "MAX_EXTENSION_VALUE_BYTES",
    "MAX_EXTENSIONS_TOTAL_BYTES",
    "EXTENSION_MAX_DEPTH",
    "clear_extensions",
)

#: Tokens the API reference guide must carry: constants, wire semantics, and path pointers.
GUIDE_REQUIRED_TOKENS: tuple[str, ...] = (
    "EXTENSIONS_KEY",
    "EXTENSION_KEY_PATTERN",
    "MAX_EXTENSION_KEYS",
    "MAX_EXTENSION_VALUE_BYTES",
    "MAX_EXTENSIONS_TOTAL_BYTES",
    "EXTENSION_MAX_DEPTH",
    "clear_extensions",
    "merge",
    "no-op",
    "first-class",
    "builder",
)


def _read(rel_path: str) -> str:
    """Return a repo-relative file's text.

    Args:
        rel_path: Path relative to the repository root.

    Returns:
        The file's decoded text.
    """
    return (REPO_ROOT / rel_path).read_text(encoding=SOURCE_ENCODING)


def _conversation_config_node() -> ast.ClassDef:
    """Return the ``ConversationConfig`` class node parsed from source (no import).

    Returns:
        The class definition node.

    Raises:
        AssertionError: When the class is absent from the agent schema module.
    """
    tree = ast.parse(_read(AGENT_MODEL_REL))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == CONVERSATION_CONFIG:
            return node
    raise AssertionError(f"{CONVERSATION_CONFIG} missing from {AGENT_MODEL_REL}")


def _conversation_config_fields() -> set[str]:
    """Return every annotated ``ConversationConfig`` field name (textual, no import)."""
    fields: set[str] = set()
    for stmt in _conversation_config_node().body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.add(stmt.target.id)
    return fields


def _passthrough_paths() -> set[str]:
    """Return dotted paths of ``ConversationConfig`` fields typed as free-form Any dicts."""
    found: set[str] = set()
    for stmt in _conversation_config_node().body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        if PASSTHROUGH_ANNOTATION.fullmatch(ast.unparse(stmt.annotation)) is not None:
            found.add(f"{CONVERSATION_CONFIG}.{stmt.target.id}")
    return found


def _module_assignment_value(tree: ast.Module, name: str) -> ast.expr | None:
    """Return the value assigned to a module-level name (``Assign`` or ``AnnAssign``).

    Args:
        tree: Parsed module to search (top-level statements only).
        name: The variable name to resolve.

    Returns:
        The assigned expression, or ``None`` when the name is never assigned.
    """
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == name:
                return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return node.value
    return None


def _string_set(expr: ast.expr) -> set[str]:
    """Collect the string constants of a dict's keys or a set/frozenset's elements.

    Args:
        expr: A ``Dict``, ``Set``, or ``frozenset(...)`` call expression.

    Returns:
        The contained string values.

    Raises:
        AssertionError: When the expression has an unexpected shape.
    """
    if isinstance(expr, ast.Dict):
        return {key.value for key in expr.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    items: list[ast.expr] = []
    if isinstance(expr, ast.Set):
        items = expr.elts
    elif (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "frozenset"
        and len(expr.args) == 1
        and isinstance(expr.args[0], ast.Set)
    ):
        items = expr.args[0].elts
    else:
        raise AssertionError(f"unsupported map/set expression: {ast.dump(expr)}")
    return {item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)}


def _settings_truth_maps() -> tuple[set[str], set[str]]:
    """Parse the Slice D gate's consumer map keys and removed-field values (no import).

    Returns:
        ``(FIELD_CONSUMERS keys, REMOVED_FIELDS values)`` read from source text.

    Raises:
        AssertionError: When either map is missing or misshapen.
    """
    tree = ast.parse(_read(SETTINGS_TRUTH_REL))
    consumers_value = _module_assignment_value(tree, FIELD_CONSUMERS_NAME)
    removed_value = _module_assignment_value(tree, REMOVED_FIELDS_NAME)
    assert consumers_value is not None, f"{FIELD_CONSUMERS_NAME} missing from {SETTINGS_TRUTH_REL}"
    assert removed_value is not None, f"{REMOVED_FIELDS_NAME} missing from {SETTINGS_TRUTH_REL}"
    return _string_set(consumers_value), _string_set(removed_value)


def _top_level_functions(source: str) -> dict[str, list[str]]:
    """Split source into top-level ``def`` segments (name → lines, no import).

    Args:
        source: The module's full source text.

    Returns:
        Each top-level function name mapped to its lines, through the next
        top-level ``def`` (module-level code belongs to no segment).
    """
    lines = source.splitlines()
    starts: dict[str, int] = {}
    order: list[str] = []
    for index, line in enumerate(lines):
        match = DEF_PATTERN.match(line)
        if match is not None:
            starts[match.group(1)] = index
            order.append(match.group(1))
    segments: dict[str, list[str]] = {}
    for position, name in enumerate(order):
        end = starts[order[position + 1]] if position + 1 < len(order) else len(lines)
        segments[name] = lines[starts[name] : end]
    return segments


def _namespace_files(root_rel: str) -> list[str]:
    """Return repo-relative ``.py`` paths under ``root_rel`` referencing the namespace.

    Args:
        root_rel: Directory relative to the repository root.

    Returns:
        Sorted repo-relative paths whose text matches ``NAMESPACE_PATTERN``.
    """
    root = REPO_ROOT / root_rel
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if NAMESPACE_PATTERN.search(path.read_text(encoding=SOURCE_ENCODING)) is not None:
            found.append(path.relative_to(REPO_ROOT).as_posix())
    return found


def _openapi_conversation_block() -> str:
    """Return the raw ``ConversationConfig`` schema block from ``openapi.yaml``."""
    text = _read(OPENAPI_REL)
    start = text.index(SCHEMA_BLOCK_MARKER)
    tail = text[start + len(SCHEMA_BLOCK_MARKER) :]
    match = SCHEMA_BLOCK_END.search(tail)
    assert match is not None, "ConversationConfig block has no terminating schema entry"
    return tail[: match.start()]


def _openapi_extensions_region(block: str) -> str:
    """Return the ``extensions`` property lines inside the openapi schema block.

    Args:
        block: The ``ConversationConfig`` schema block.

    Returns:
        The property's lines, through the next sibling property.

    Raises:
        AssertionError: When the property is absent from the block.
    """
    lines = block.splitlines()
    begin: int | None = None
    for index, line in enumerate(lines):
        if line == OPENAPI_PROPERTY_PREFIX:
            begin = index
            break
    assert begin is not None, "openapi ConversationConfig block documents no `extensions` property"
    end = len(lines)
    for index in range(begin + 1, len(lines)):
        if OPENAPI_PROPERTY_END.match("\n" + lines[index]) is not None:
            end = index
            break
    return "\n".join(lines[begin:end])


def _api_reference_conversation_section() -> str:
    """Return the ``ConversationConfig`` section from ``API_REFERENCE.md``."""
    text = _read(API_REFERENCE_REL)
    start = text.index(DOCS_SECTION_MARKER)
    tail = text[start + len(DOCS_SECTION_MARKER) :]
    end = tail.index("\n### ")
    return tail[:end]


def _api_reference_extensions_guide(section: str) -> str:
    """Return the extensions guide inside the API reference section.

    Args:
        section: The ``ConversationConfig`` reference section.

    Returns:
        The guide from its marker heading to the section end.

    Raises:
        AssertionError: When the guide heading is absent.
    """
    assert GUIDE_MARKER in section, "API reference documents no extensions guide"
    return section[section.index(GUIDE_MARKER) :]


def test_only_allowlisted_passthroughs_in_validated_schema() -> None:
    """Any free-form subtree beyond the allowlist fails (no second passthrough)."""
    found = _passthrough_paths()
    extra = found - set(ALLOWED_PASSTHROUGHS)
    assert not extra, (
        "second passthrough in the validated agent schema (spec 0043 allows only "
        f"{sorted(ALLOWED_PASSTHROUGHS)}): {sorted(extra)}"
    )


def test_extensions_passthrough_present() -> None:
    """The reserved namespace exists exactly where the contract says (Slice A)."""
    assert CONVERSATION_CONFIG + "." + EXTENSIONS_FIELD in _passthrough_paths(), (
        f"{CONVERSATION_CONFIG}.{EXTENSIONS_FIELD} must be a free-form dict[str, Any] "
        f"in {AGENT_MODEL_REL} (spec 0043 Slice A)"
    )


def test_extension_bounds_named_as_constants() -> None:
    """Every bound literal is named in the agents constants module (Slice A, Rule 1b)."""
    text = _read(AGENTS_CONSTANTS_REL)
    missing = [name for name in EXTENSION_CONSTANT_NAMES if name not in text]
    assert missing == [], f"extension bounds must be named constants: missing {missing}"


def test_audit_walk_names_the_extensions_exemption() -> None:
    """The catalog/audit walk carries the one named exemption, skipped by key (Slice B)."""
    text = _read(STATIC_METHODS_REL)
    assert "EXTENSIONS_KEY" in text, (
        f"audit exemption must reference EXTENSIONS_KEY in {STATIC_METHODS_REL} (spec 0043 Slice B)"
    )
    segments = _top_level_functions(text)
    assert any("extension" in line.lower() for lines in segments.values() for line in lines), (
        f"no audit segment in {STATIC_METHODS_REL} names its extensions skip (spec 0043 Slice B)"
    )
    assert any(
        SKIP_BY_KEY_PATTERN.search(line) is not None
        for name, lines in segments.items()
        if name.startswith(AUDIT_FUNCTION_PREFIXES)
        for line in lines
    ), "audit walk must skip the extensions subtree by key, never by value-sniffing (spec 0043 Slice B)"


def test_audit_helpers_never_value_read_extensions() -> None:
    """Every extensions mention inside audit segments is the skip, never a value read."""
    segments = _top_level_functions(_read(STATIC_METHODS_REL))
    offenders: list[str] = []
    for name, lines in segments.items():
        if not name.startswith(AUDIT_FUNCTION_PREFIXES):
            continue
        for relative, line in enumerate(lines, start=1):
            if "extension" in line.lower() and not any(hint in line.lower() for hint in EXEMPTION_LINE_HINTS):
                offenders.append(f"{name}+{relative}: {line.strip()}")
    assert offenders == [], (
        "catalog/audit read descending into extensions (the walk skips by key only):\n" + "\n".join(offenders)
    )


def test_catalog_never_names_extensions() -> None:
    """Provider/model catalogs never see tenant keys (registry pattern stays ours)."""
    hits = _namespace_files(AGENTS_REL.replace("agents", "catalog"))
    assert hits == [], f"catalog tree references the tenant namespace: {hits}"


def test_extensions_mentions_stay_inside_allowlisted_files() -> None:
    """No engine, catalog, legacy, or cross-module read of the namespace exists."""
    found = set(_namespace_files("voiceai"))
    extra = found - set(EXTENSIONS_MENTION_ALLOWLIST)
    assert not extra, (
        "extensions namespace referenced outside the allowlist "
        "(engine ignores these keys; catalog never sees them):\n" + "\n".join(sorted(extra))
    )


def test_non_extension_fields_keep_consumer_or_absent_pin() -> None:
    """Every other schema field is consumer-mapped or removed (Slice D pin holds)."""
    schema_fields = _conversation_config_fields()
    consumers, _ = _settings_truth_maps()
    assert EXTENSIONS_FIELD not in consumers, (
        f"{EXTENSIONS_FIELD} is engine-ignored: it must never gain a consumer entry (spec 0043)"
    )
    assert schema_fields - {EXTENSIONS_FIELD} == consumers, (
        "settings truth drift on non-extension fields (spec 0042: wire the key or delete it):\n"
        + "\n".join(f"+ {field} (in schema, missing a consumer)" for field in sorted(schema_fields - {EXTENSIONS_FIELD} - consumers))
        + "\n".join(f"- {field} (mapped, gone from schema)" for field in sorted(consumers - schema_fields))
    )


def test_removed_fields_stay_absent() -> None:
    """Proven-dead keys stay out, including in the extensions world (prove-or-remove)."""
    _, removed = _settings_truth_maps()
    survivors = removed & _conversation_config_fields()
    assert not survivors, f"dead keys resurrected on {CONVERSATION_CONFIG}: {sorted(survivors)}"


def test_extensions_documented_in_openapi() -> None:
    """The openapi schema block documents the namespace with constant names."""
    region = _openapi_extensions_region(_openapi_conversation_block())
    missing = [name for name in OPENAPI_REQUIRED_NAMES if name not in region]
    assert missing == [], f"openapi extensions property must cite {missing} by name (never numbers)"


def test_extensions_documented_in_api_reference() -> None:
    """The API reference tables the field and guides syntax, writes, and graduation."""
    section = _api_reference_conversation_section()
    assert f"`{EXTENSIONS_FIELD}`" in section, "API reference ConversationConfig table lacks `extensions`"
    guide = _api_reference_extensions_guide(section).lower()
    missing = [token for token in GUIDE_REQUIRED_TOKENS if token.lower() not in guide]
    assert missing == [], f"extensions guide must cover {missing}"
    assert "graduat" in guide, "extensions guide must name the prove-or-promote graduation path"


def test_docs_name_constants_never_numbers() -> None:
    """Bound numbers live in constants only; docs cite names so tuning never desyncs."""
    regions = (
        ("openapi.yaml", _openapi_extensions_region(_openapi_conversation_block())),
        ("API_REFERENCE.md", _api_reference_extensions_guide(_api_reference_conversation_section())),
    )
    for label, region in regions:
        match = RAW_BOUND_PATTERN.search(region)
        assert match is None, f"{label} extensions docs duplicate bound number {match.group(0) if match else ''}"
