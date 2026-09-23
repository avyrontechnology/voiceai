"""Wire DTOs for the voice module: HTTP request/response shapes, strictly typed (T4).

Talko parity (`TalkoContract` in each component's `dto.py`): the HTTP boundary
shapes moved from `models.py` (same names, fields and constraints — including the
`provider` literal), not the persisted documents (`PlacedCall`,
`TalkoPartnerConfig` stay in `models.py`) and not the realtime packet views
(`TurnMeta`, `WsDataPacket`, ... stay too).

Service signatures take the request DTOs, so the service imports them from here;
response DTOs shape controller `response_model` entries. Controllers never import
`models.py` for wire shapes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = ["VoiceContract"]


class VoiceContract:
    """Namespace for the voice wire shapes (talko `TalkoContract` shape, strict)."""

    class PlaceCallRequest(BaseModel):
        """One outbound call request (spec 0008).

        Module-level input model (never persisted): the controller validates it at the
        boundary and the service resolves credentials per call. Field-for-field
        compatible with the legacy ``SimulateCallRequest``/``PlaceCallRequest`` shape
        the UI already sends, so no UI change is needed.
        """

        agent_id: str = Field(..., min_length=1)
        to_number: str = Field(..., min_length=1)
        from_number: str | None = None
        variables: dict[str, Any] = Field(default_factory=dict)  # why: free-form per-contact data
        provider: Literal["simulated", "talko"] = "simulated"
        partner_id: str | None = None
        talko_api_key: str | None = None
        delay_scale: float = Field(0.5, ge=0.0)

    class CreateTalkoPartnerRequest(BaseModel):
        """Body for creating a partner record (spec 0008)."""

        partner_id: str = Field(..., min_length=1)
        display_name: str = ""
        talko_api_base_url: str | None = None
        talko_api_key: str = Field(..., min_length=1)
        default_did: str | None = None
        dids: list[str] = Field(default_factory=list)
        vendor_config_id: str | None = None

    class UpdateTalkoPartnerRequest(BaseModel):
        """Body for updating a partner record (spec 0008).

        All fields optional; an omitted or empty `talko_api_key` keeps the stored
        key (rotation is explicit, never accidental).
        """

        display_name: str | None = None
        talko_api_base_url: str | None = None
        talko_api_key: str | None = None
        default_did: str | None = None
        dids: list[str] | None = None
        vendor_config_id: str | None = None

    class TalkoPartnerView(BaseModel):
        """Secret-free projection of a partner record for API responses (spec 0008).

        The key itself never leaves the repository toward clients; operators get a
        configured flag plus a last-4 hint for sanity checks.
        """

        partner_id: str
        display_name: str = ""
        talko_api_base_url: str | None = None
        default_did: str | None = None
        dids: list[str] = Field(default_factory=list)
        vendor_config_id: str | None = None
        key_configured: bool = False
        key_hint: str | None = None

    class TalkoPartnerListResponse(BaseModel):
        """Paginated-list envelope for partner records (spec 0008)."""

        partners: list[VoiceContract.TalkoPartnerView] = Field(default_factory=list)

    class ConnectTalkoPartnerRequest(BaseModel):
        """Body for the one-click connect flow (spec 0009).

        The operator pastes a partner key (plus an optional nickname); the service
        validates it against talko-service, fetches the partner's DIDs, and upserts
        the record — no hand-typed DIDs, base URLs, or vendor ids.
        """

        talko_api_key: str = Field(..., min_length=1)
        display_name: str = ""
        partner_id: str | None = None
        talko_api_base_url: str | None = None

    class TalkoPartnerPreview(BaseModel):
        """Key-free preview of what connecting would store (spec 0009)."""

        partner_id: str | None = None
        dids: list[str] = Field(default_factory=list)
        display_name: str = ""
