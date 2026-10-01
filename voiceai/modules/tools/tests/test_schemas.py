"""Payload shapes (spec 0049): literal kinds at request validation; the backstop hides pydantic text."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from voiceai.modules.tools.errors import InvalidToolError
from voiceai.modules.tools.helpers import validation_field_paths
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.schemas import CreateToolPayload, UpdateToolPayload


def test_known_kinds_render_rows() -> None:
    """`function`/`webhook` payloads become rows keyed `{kind}:{name}`; `function` is the default."""
    row = CreateToolPayload(kind="webhook", name="notify", url="https://hooks.test/n").to_definition()

    assert row.tool_id == "webhook:notify"
    assert row.kind == "webhook"
    assert row.url == "https://hooks.test/n"
    assert UpdateToolPayload(name="t").to_definition().kind == "function"


def test_unknown_kind_fails_at_request_validation() -> None:
    """A legacy-shaped body is a `ValidationError` on the payload itself — FastAPI's 422, not a 500."""
    with pytest.raises(ValidationError) as excinfo:
        CreateToolPayload(name="t", kind="datetime")  # type: ignore[arg-type]  # why: the wire can send any string

    assert validation_field_paths(excinfo.value) == ["kind"]


def test_to_definition_backstop_names_fields_not_messages() -> None:
    """A payload the request schema never validated answers 400 with field paths only."""
    payload = CreateToolPayload.model_construct(name="t", kind="datetime")

    with pytest.raises(InvalidToolError) as excinfo:
        payload.to_definition()

    err = excinfo.value
    assert err.http_status == 400
    assert err.details == {"fields": ["kind"]}
    assert "datetime" not in err.public_message
    assert isinstance(err.__cause__, ValidationError)


def test_validation_field_paths_are_dotted_sorted_and_unique() -> None:
    """Nested locations join with dots; duplicates collapse; order is stable."""
    with pytest.raises(ValidationError) as excinfo:
        ToolDefinition(tool_id="x", kind="nope", name="n", timeout_s="slow")  # type: ignore[arg-type]  # why: pins the reducer

    assert validation_field_paths(excinfo.value) == ["kind", "timeout_s"]
