"""Wallet and template error classes."""

from voiceai.common.errors import NotFoundError


class WalletError(NotFoundError):
    """Base error for the wallet module (404 family; raised across layer boundaries)."""


class TemplateNotFoundError(WalletError):
    """Raised when a requested template is not found."""

    def __init__(self, template_id: str) -> None:
        super().__init__(message=f"Template not found: {template_id}", details={"template_id": template_id})
