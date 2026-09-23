"""Wire-contract pins: bodies validate through the DTOs, garbage does not (T5)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from voiceai.modules.wallet import controller
from voiceai.modules.wallet.schemas import WalletContract


def test_topup_request_rejects_non_positive() -> None:
    """The top-up body validates before any balance math runs."""
    assert WalletContract.TopUpRequest.model_validate({"amount_credits": 5}).amount_credits == 5.0
    with pytest.raises(ValidationError):
        WalletContract.TopUpRequest.model_validate({"amount_credits": 0})


def test_response_models_reference_the_contract() -> None:
    """Fixed-shape HTTP routes document their DTOs (import-template excepted)."""
    documented: dict[tuple[str, str], Any] = {}  # why: response_model entries are DTO classes
    for route in controller.router.routes:
        model = getattr(route, "response_model", None)
        methods: set[str] = getattr(route, "methods", set())
        if model is not None and methods:
            documented[(getattr(route, "path", ""), next(iter(methods)))] = model
    assert documented[("/wallet", "GET")].__name__ == "Wallet"
    assert documented[("/wallet/topup", "POST")].__name__ == "Wallet"
    assert documented[("/wallet/ledger", "GET")].__name__ == "LedgerListResponse"
    assert documented[("/templates", "GET")].__name__ == "TemplateListResponse"
    assert documented[("/templates/{template_id}", "GET")].__name__ == "Template"
