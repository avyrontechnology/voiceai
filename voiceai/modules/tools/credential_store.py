"""Vault-backed credential store for the executor (spec 0059).

`VaultCredentialStore` implements `CredentialStorePort` over tenant-scoped
vault rows: `auth_ref` pins to `cred:<id>` (anything else fails closed —
never a bare call), the row decrypts through `CredentialVault`, and the
envelope composes `{credential_type, auth_config, **material}` for the
type-switched builder in `auth_resolver`. Unknown ids, foreign rows, and
corrupt envelopes all answer `None` (the resolver raises identifiers-only);
nothing secret is ever logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from voiceai.common.logger import get_logger
from voiceai.modules.tools import constants as C
from voiceai.modules.tools.vault import CredentialVault
from voiceai.modules.tools.vault_repository import VaultRepository

__all__ = ["VaultCredentialStore"]

logger: logging.Logger = get_logger(C.MODULE_NAME)


class VaultCredentialStore:
    """`CredentialStorePort` over one tenant's vault views + vault.

    Args:
        repository: Vault views bound to the ambient request tenant (never
            system views — cross-tenant rows must read as missing).
        vault: Envelope decryption (fails closed when dark).
    """

    def __init__(self, repository: VaultRepository, vault: CredentialVault) -> None:
        self._repository = repository
        self._vault = vault

    async def fetch_credential(self, auth_ref: str) -> Mapping[str, Any] | None:  # why: vault JSON is open
        """Return the composed envelope for `cred:<id>`, or `None`.

        Args:
            auth_ref: The secret-store pointer carried by the tool row.

        Returns:
            `{credential_type, auth_config, **material}`, or `None` when the
            format is foreign, the row is unknown/foreign, or the envelope
            cannot be opened or decoded.
        """
        if not auth_ref.startswith(C.AUTH_REF_PREFIX):
            return None
        credential_id = auth_ref[len(C.AUTH_REF_PREFIX) :]
        if not credential_id:
            return None
        row = await self._repository.get_credential(credential_id)
        if row is None:
            return None
        try:
            plaintext = self._vault.decrypt(row.encrypted_data, key_version=row.key_version)
            material = json.loads(plaintext.decode("utf-8"))
        except Exception as exc:  # corrupt envelope or dark vault — fail closed, log the type only
            logger.warning("credential open failed (%s)", type(exc).__name__)
            return None
        if not isinstance(material, Mapping):
            logger.warning("credential open failed (%s)", "shape")
            return None
        envelope: dict[str, Any] = {  # why: vault JSON is open
            "credential_type": row.credential_type,
            "auth_config": dict(row.auth_config),
        }
        envelope.update({str(key): value for key, value in material.items()})
        return envelope
