"""Every literal the privacy module uses: identity, routes, purposes, and log templates."""

from __future__ import annotations

from typing import Final

# --- Module identity -------------------------------------------------------------------
MODULE_NAME: Final[str] = "privacy"
ROUTE_PREFIX: Final[str] = "/privacy"
CONSENT_PATH: Final[str] = "/consent"
CONSENT_SUBJECT_PATH: Final[str] = "/consent/{subject_id}"
CONSENT_WITHDRAW_PATH: Final[str] = "/consent/{subject_id}/{purpose}"

# --- Consent purposes (closed vocabulary — new purposes arrive via spec, never free text) --
PURPOSE_ANALYTICS: Final[str] = "analytics"
PURPOSE_MARKETING: Final[str] = "marketing"
PURPOSE_THIRD_PARTY_SHARING: Final[str] = "third_party_sharing"
PURPOSE_VOICE_RECORDING: Final[str] = "voice_recording"

KNOWN_PURPOSES: Final[tuple[str, ...]] = (
    PURPOSE_ANALYTICS,
    PURPOSE_MARKETING,
    PURPOSE_THIRD_PARTY_SHARING,
    PURPOSE_VOICE_RECORDING,
)

# --- Wire field names ------------------------------------------------------------------
SUBJECT_ID_FIELD: Final[str] = "subject_id"
PURPOSE_FIELD: Final[str] = "purpose"
POLICY_VERSION_FIELD: Final[str] = "policy_version"
GRANTED_FIELD: Final[str] = "granted"

# --- Scopes ----------------------------------------------------------------------------
SCOPE_PLATFORM_READ: Final[str] = "platform:read"
SCOPE_PLATFORM_WRITE: Final[str] = "platform:write"

# --- Wire protocol -----------------------------------------------------------------------
AUTHORIZATION_HEADER: Final[str] = "authorization"

# --- Errors (client-visible: generic by design, never internals) -------------------------
UNKNOWN_PURPOSE_MESSAGE: Final[str] = "Unknown consent purpose"
CONSENT_NOT_FOUND_MESSAGE: Final[str] = "Consent record not found"

# --- Log templates (%-style: the logger formats them only when the record is emitted) ---
CONSENT_GRANTED_LOG: Final[str] = "consent granted subject=%s purpose=%s"
CONSENT_WITHDRAWN_LOG: Final[str] = "consent withdrawn subject=%s purpose=%s"
