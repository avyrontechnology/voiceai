"""Turn/response identity stamping: sequence, turn, and group uids (spec 0033).

Verbatim move of `TaskManager.__get_updated_meta_info` /
`_spawn_followup_meta_info`: transcript and tool-call grouping need a
response-turn id stable across all chunks and sub-steps of one response
chain, independent from audio sequencing (`sequence_id` = interruption/audio
gating, `chunk_id` = chunking, `turn_id` = transcript/history grouping).
Logger channel moves to `otobaai.voice` per the move discipline; every
message string is verbatim.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME

__all__ = ["MetaSession", "get_updated_meta_info", "spawn_followup_meta_info"]

logger = get_logger(MODULE_NAME)


class MetaSession(Protocol):
    """The session surface the stamping bodies touch (duck-typed at runtime)."""

    tools: Any
    interruption_manager: Any
    _response_turn_id: int


def get_updated_meta_info(session: MetaSession, meta_info: Any = None) -> Any:
    """Stamp a fresh response identity onto copied meta info.

    Used in case there's silence from callee's side: with no incoming meta,
    the transcriber's own meta seeds the copy.

    Args:
        session: The live call session (duck-typed `MetaSession`).
        meta_info: Incoming meta, or `None` to seed from the transcriber.

    Returns:
        The stamped copy (fresh `sequence_id`, `turn_id`, `response_uid`,
        group uid; parent uid dropped).
    """
    # This is used in case there's silence from callee's side
    if meta_info is None:
        meta_info = session.tools["transcriber"].get_meta_info()
        logger.info(f"Metainfo {meta_info}")
    meta_info_copy = meta_info.copy()

    new_sequence_id = session.interruption_manager.get_next_sequence_id()
    meta_info_copy["sequence_id"] = new_sequence_id
    # Transcript/tool-call grouping needs a response-turn id that is stable
    # across all chunks and sub-steps of one response chain, but independent
    # from audio sequencing. sequence_id is for interruption/audio gating;
    # chunk_id is for chunking; turn_id is for transcript/history grouping.
    session._response_turn_id += 1
    meta_info_copy["turn_id"] = session._response_turn_id
    response_uid = str(uuid.uuid4())
    meta_info_copy["response_uid"] = response_uid
    meta_info_copy["response_group_uid"] = response_uid
    meta_info_copy.pop("parent_response_uid", None)
    logger.info(
        "VOICEAI_TRACE_META new_response seq=%s turn=%s response_uid=%s group_uid=%s request_id=%s origin=%s",
        meta_info_copy.get("sequence_id"),
        meta_info_copy.get("turn_id"),
        meta_info_copy.get("response_uid"),
        meta_info_copy.get("response_group_uid"),
        meta_info_copy.get("request_id"),
        meta_info_copy.get("origin"),
    )

    return meta_info_copy


def spawn_followup_meta_info(session: MetaSession, meta_info: Any) -> Any:
    """Derive a followup response identity linked to its parent.

    Drops per-chunk keys (a followup starts a fresh chunk stream) and links
    the group/parent uids so transcript grouping can walk the chain.

    Args:
        session: The live call session (duck-typed `MetaSession`).
        meta_info: The parent response's meta info.

    Returns:
        The followup meta info, linked to the parent.
    """
    followup_meta_info = get_updated_meta_info(session, meta_info)
    followup_meta_info["response_group_uid"] = meta_info.get("response_group_uid") or meta_info.get("response_uid")
    followup_meta_info["parent_response_uid"] = meta_info.get("response_uid")
    for key in (
        "chunk_id",
        "mark_id",
        "is_first_chunk",
        "is_first_chunk_of_entire_response",
        "is_final_chunk_of_entire_response",
        "end_of_synthesizer_stream",
        "end_of_llm_stream",
        "text_synthesized",
    ):
        followup_meta_info.pop(key, None)
    logger.info(
        "VOICEAI_TRACE_META followup seq=%s turn=%s response_uid=%s group_uid=%s "
        "parent_response_uid=%s request_id=%s parent_seq=%s parent_turn=%s",
        followup_meta_info.get("sequence_id"),
        followup_meta_info.get("turn_id"),
        followup_meta_info.get("response_uid"),
        followup_meta_info.get("response_group_uid"),
        followup_meta_info.get("parent_response_uid"),
        followup_meta_info.get("request_id"),
        meta_info.get("sequence_id"),
        meta_info.get("turn_id"),
    )
    return followup_meta_info
