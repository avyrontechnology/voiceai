"""Tools wire shapes: request bodies (spec 0029, slice 1; spec 0049 validation).

`kind` is typed with the `ToolKind` literal so an unknown kind fails FastAPI
request validation (the 422 envelope) instead of the row model raising outside
request validation — an opaque 500 before spec 0049. `to_definition` keeps a
backstop for payload/row drift: a residual `ValidationError` becomes the
module's `InvalidToolError` carrying field paths only, never pydantic text.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from voiceai.modules.tools import constants as C
from voiceai.modules.tools.constants import ToolKind
from voiceai.modules.tools.errors import InvalidToolError
from voiceai.modules.tools.helpers import validation_field_paths
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.static_methods import build_tool_id

__all__ = ["CreateToolPayload", "UpdateToolPayload"]


class CreateToolPayload(BaseModel):
    """Body for `POST /tools` — everything except id/tenant (server-stamped)."""

    model_config = {"extra": "forbid"}

    kind: ToolKind = Field(default=C.TOOL_KIND_FUNCTION, description=C.KIND_FIELD_DESCRIPTION)
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    parameters: dict = Field(default_factory=dict)  # why: JSON-Schema contracts are free-form JSON
    url: str | None = Field(default=None)
    method: str = Field(default=C.DEFAULT_TOOL_METHOD)
    auth_ref: str | None = Field(default=None)
    timeout_s: int = Field(default=C.TOOL_TIMEOUT_S, ge=C.TOOL_TIMEOUT_MIN_S, le=C.TOOL_TIMEOUT_MAX_S)
    deprecated: bool = Field(default=False)

    def to_definition(self) -> ToolDefinition:
        """Render the payload as a storable row (tenant stamped by the service).

        Returns:
            The row, id built from `{kind}:{name}`.

        Raises:
            InvalidToolError: When the row model rejects a payload the request
                schema accepted (payload/row drift): HTTP 400 whose details carry
                the offending field paths only, never pydantic text (spec 0049).
        """
        try:
            return ToolDefinition(
                tool_id=build_tool_id(self.kind, self.name),
                kind=self.kind,
                name=self.name,
                description=self.description,
                parameters=dict(self.parameters),
                url=self.url,
                method=self.method,
                auth_ref=self.auth_ref,
                timeout_s=self.timeout_s,
                deprecated=self.deprecated,
            )
        except ValidationError as exc:
            raise InvalidToolError(
                C.DEFINITION_REJECTED_MESSAGE,
                details={C.DETAIL_KEY_FIELDS: validation_field_paths(exc)},
                cause=exc,
            ) from exc


class UpdateToolPayload(CreateToolPayload):
    """Body for `PUT /tools/{id}` — same shape, id comes from the path."""
