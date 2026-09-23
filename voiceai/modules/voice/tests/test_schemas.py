"""Wire-contract pins: bodies validate through the DTOs, garbage does not (T4).

Response DTOs double as `response_model` entries on the fixed-shape routes, so
these tests also pin that the decorators reference real contract members.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from voiceai.modules.voice import controller
from voiceai.modules.voice.schemas import VoiceContract

VALID_CONFIG_PARTNER = {"partner_id": "2", "talko_api_key": "tkp_live_x"}


def test_place_call_request_validates_digits_shape() -> None:
    """The outbound body validates; an empty destination fails DTO validation."""
    valid = VoiceContract.PlaceCallRequest.model_validate(
        {"agent_id": "a", "to_number": "+919812345678", "delay_scale": 0}
    )
    assert valid.provider == "simulated"

    with pytest.raises(ValidationError):
        VoiceContract.PlaceCallRequest.model_validate({"agent_id": "a", "to_number": ""})
    with pytest.raises(ValidationError):
        VoiceContract.PlaceCallRequest.model_validate(
            {"agent_id": "a", "to_number": "+911", "provider": "smoke-signals"}
        )


def test_partner_dtos_validate() -> None:
    """Partner create/update/connect bodies validate through the contract."""
    created = VoiceContract.CreateTalkoPartnerRequest.model_validate(VALID_CONFIG_PARTNER)
    assert created.partner_id == "2"

    with pytest.raises(ValidationError):
        VoiceContract.CreateTalkoPartnerRequest.model_validate({"partner_id": "2"})
    assert VoiceContract.UpdateTalkoPartnerRequest.model_validate({}).talko_api_key is None


def test_response_models_reference_the_contract() -> None:
    """Fixed-shape HTTP routes document their DTOs (websocket + delete excepted)."""
    documented: dict[tuple[str, str], Any] = {}
    for route in controller.router.routes:
        model = getattr(route, "response_model", None)
        methods: set[str] = getattr(route, "methods", set())
        if model is not None and methods:
            documented[(getattr(route, "path", ""), next(iter(methods)))] = model
    assert documented[("/calls/place", "POST")].__name__ == "PlacedCall"
    assert documented[("/talko/partners", "GET")].__name__ == "TalkoPartnerListResponse"
    assert documented[("/talko/partners", "POST")].__name__ == "TalkoPartnerView"
    assert documented[("/talko/partners/preview", "POST")].__name__ == "TalkoPartnerPreview"
