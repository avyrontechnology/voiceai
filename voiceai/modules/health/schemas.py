"""Wire DTOs for the health module: every request/response shape, strictly typed (T1).

Talko parity (`TalkoContract` in each component's `dto.py`): the controller's
`response_model` entries and the tests' shape pins both come from here — never from
the persistence/domain models in `models.py`. The only shared type is the
`HealthState` enum itself, so the wire can never drift from the probe verdicts.

Health takes no request bodies (all three endpoints are bare `GET`s), so this
contract holds responses only.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from voiceai.modules.health.models import HealthState

__all__ = ["HealthContract"]


class HealthContract:
    """Namespace for the health wire shapes (talko `TalkoContract` shape, strict).

    Controllers reference these as `response_model`; services and repositories never
    import this module (AGENTS.md §3 — the DTO direction is controller-in only).
    """

    class ComponentResponse(BaseModel):
        """One dependency's probe outcome, as clients read it inside the envelope."""

        name: str = Field(..., min_length=1, description="Component identifier (a COMPONENT_* constant).")
        state: HealthState = Field(..., description="Probe verdict.")
        detail: str | None = Field(None, description="Short, client-safe explanation. Never exception text.")
        latency_ms: float | None = Field(None, ge=0, description="Probe round trip in milliseconds.")

    class ReportResponse(BaseModel):
        """Aggregate dependency report: the readiness payload and the root payload."""

        status: HealthState = Field(..., description="Folded state across components.")
        components: list[HealthContract.ComponentResponse] = Field(
            ..., description="Per-dependency results, in probe order."
        )
        version: str = Field(..., min_length=1, description="Running application version.")
        uptime_s: float = Field(..., ge=0, description="Seconds since process start.")

    class LivenessResponse(BaseModel):
        """Minimal orchestrator signal: one field, no dependency traffic."""

        status: HealthState = Field(..., description="Process liveness (always UP when answered).")
