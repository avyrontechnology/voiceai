"""Re-export shim — models merged into `models.py` (spec 0062-hygiene-pass).

Backward-compat imports for code that referenced `vault_models`.
"""

from __future__ import annotations

from voiceai.modules.tools.models import (
    CREDENTIAL_TYPE_API_KEY,
    CREDENTIAL_TYPE_BASIC,
    CREDENTIAL_TYPE_BEARER,
    CREDENTIAL_TYPE_CUSTOM,
    CREDENTIAL_TYPE_NONE,
    CREDENTIAL_TYPE_OAUTH2,
    INTEGRATION_STATUS_ACTIVE,
    CredentialType,
    IntegrationTemplate,
    PartnerCredential,
    PartnerIntegration,
)

__all__ = [
    "CREDENTIAL_TYPE_API_KEY",
    "CREDENTIAL_TYPE_BASIC",
    "CREDENTIAL_TYPE_BEARER",
    "CREDENTIAL_TYPE_CUSTOM",
    "CREDENTIAL_TYPE_NONE",
    "CREDENTIAL_TYPE_OAUTH2",
    "CredentialType",
    "INTEGRATION_STATUS_ACTIVE",
    "IntegrationTemplate",
    "PartnerCredential",
    "PartnerIntegration",
]
