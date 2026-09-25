"""Tools wire shapes: request bodies (spec 0029, slice 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.static_methods import build_tool_id

__all__ = ["CreateToolPayload", "UpdateToolPayload"]


class CreateToolPayload(BaseModel):
    """Body for `POST /tools` — everything except id/tenant (server-stamped)."""

    model_config = {"extra": "forbid"}

    kind: str = Field(default="function", description="`function` or `webhook` (`internal` rejected).")
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    parameters: dict = Field(default_factory=dict)  # why: JSON-Schema contracts are free-form JSON
    url: str | None = Field(default=None)
    method: str = Field(default="POST")
    auth_ref: str | None = Field(default=None)
    timeout_s: int = Field(default=10, ge=1, le=120)
    deprecated: bool = Field(default=False)

    def to_definition(self) -> ToolDefinition:
        """Render the payload as a storable row (tenant stamped by the service)."""
        return ToolDefinition(
            tool_id=build_tool_id(self.kind, self.name),
            kind=self.kind,  # type: ignore[arg-type]  # why: service validates the kind before persisting
            name=self.name,
            description=self.description,
            parameters=dict(self.parameters),
            url=self.url,
            method=self.method,
            auth_ref=self.auth_ref,
            timeout_s=self.timeout_s,
            deprecated=self.deprecated,
        )


class UpdateToolPayload(CreateToolPayload):
    """Body for `PUT /tools/{id}` — same shape, id comes from the path."""
