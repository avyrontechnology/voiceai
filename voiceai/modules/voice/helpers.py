"""Small mapping helpers between the engine's raw dicts and the typed views (rule 1g).

Pure mappings only: raw ``{"data", "meta_info"}`` packets in, `models` views out. The
raw dicts stay the wire truth — nothing here rewrites a packet.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from voiceai.modules.voice.constants import (
    ASR_TURN_ID_KEY,
    CONTENT_KEY,
    DATA_KEY,
    META_INFO_KEY,
    PACKET_TYPE_KEY,
    RESPONSE_GROUP_UID_KEY,
    RESPONSE_UID_KEY,
    SEQUENCE_ID_KEY,
    TURN_ID_KEY,
)
from voiceai.modules.voice.models import TalkoPartnerConfig, TalkoPartnerView, TranscriberEvent, TurnMeta, WsDataPacket

__all__ = ["packet_view", "talko_partner_view", "transcriber_event_view", "turn_meta_from_meta_info"]


def turn_meta_from_meta_info(meta_info: Mapping[str, Any] | None) -> TurnMeta:
    """Extract the four correlation-id spaces (plus the ASR turn id) from ``meta_info``.

    Args:
        meta_info: A packet's metadata dict; ``None`` (the dashboard-playground quirk)
            yields an all-``None`` view.

    Returns:
        The `TurnMeta` view; ids the packet does not carry stay ``None``.
    """
    if meta_info is None:
        return TurnMeta()
    return TurnMeta(
        sequence_id=meta_info.get(SEQUENCE_ID_KEY),
        turn_id=meta_info.get(TURN_ID_KEY),
        response_uid=meta_info.get(RESPONSE_UID_KEY),
        response_group_uid=meta_info.get(RESPONSE_GROUP_UID_KEY),
        asr_turn_id=meta_info.get(ASR_TURN_ID_KEY),
    )


def packet_view(packet: Mapping[str, Any]) -> WsDataPacket:
    """Read a raw queue packet into the typed `WsDataPacket` view.

    Args:
        packet: A ``{"data", "meta_info"}`` dict as the engine queues carry them.

    Returns:
        The typed view; missing keys degrade to ``None`` exactly as ``.get`` reads in
        the legacy loops do.
    """
    return WsDataPacket(data=packet.get(DATA_KEY), meta_info=packet.get(META_INFO_KEY))


def transcriber_event_view(data: Any) -> TranscriberEvent:  # why: the wire slot mixes str and dict shapes
    """Normalise a transcriber-queue ``packet["data"]`` payload into one event view.

    The legacy listener branches on two shapes (task_manager's ``_listen_transcriber``):
    bare control strings, and transcript dicts carrying ``type`` + ``content``. Both
    fold into `TranscriberEvent`.

    Args:
        data: The ``data`` slot of a transcriber output packet.

    Returns:
        The event view: control strings become the event type with no content;
        transcript dicts keep their type and text.
    """
    if isinstance(data, Mapping):
        return TranscriberEvent(type=str(data.get(PACKET_TYPE_KEY, "")), content=data.get(CONTENT_KEY))
    return TranscriberEvent(type=str(data), content=None)


def talko_partner_view(partner: TalkoPartnerConfig) -> TalkoPartnerView:
    """Project a partner record to its secret-free API view (spec 0008).

    The key itself never leaves toward clients; operators get a configured
    flag plus a last-4 hint for sanity checks.

    Args:
        partner: The stored partner record (carries the secret).

    Returns:
        The `TalkoPartnerView` without the secret.
    """
    key = partner.talko_api_key or ""
    return TalkoPartnerView(
        partner_id=partner.partner_id,
        display_name=partner.display_name,
        talko_api_base_url=partner.talko_api_base_url,
        default_did=partner.default_did,
        dids=list(partner.dids),
        vendor_config_id=partner.vendor_config_id,
        key_configured=bool(key),
        key_hint=key[-4:] if key else None,
    )
