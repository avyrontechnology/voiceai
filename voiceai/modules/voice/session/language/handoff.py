"""Switch-handoff clips: prewarm, render and play the cover line (spec 0004, B9a).

The handoff bodies moved here VERBATIM from
``voiceai/agent_manager/task_manager.py`` (Region Q's handoff cluster, currently
tm 5841-5988): `play_switch_handoff` (speak the target language's handoff line to
cover the post-switch reply-generation gap), `handoff_text_for` (template
rendering), `handoff_mulaw_wire` (the wire-format decision), `prewarm_handoff_clips`
(per-language one-shot renders, concurrent with the welcome) and
`handoff_clip_convert` (the blocking decode, run off-loop). The B5-B8 seams apply
unchanged: each function takes the live call session as its first parameter (kept
named ``self`` so the bodies stay byte-identical), TaskManager keeps same-named
thin delegators (mangled ``_TaskManager__*`` spellings included) and injects
itself on every call (§3.1 bridge 3).

**The process-wide clip cache moved WITH its owner.** ``HANDOFF_CLIP_CACHE`` /
``HANDOFF_CLIP_CACHE_MAX`` were flagged at B3 as module data that relocates with
its owning subsystem (rule 1g): they now live here, and
``task_manager.py`` re-binds both names by identity (the B3
``welcome_pcm_upsampled`` precedent — the binding IS the cache object, so
``tests/test_handoff_prewarm.py``'s import-and-clear keeps operating on the one
real cache).

**This module is the lookup site** (R3) for the handoff bodies' globals —
``convert_to_request_log`` / ``update_prompt_with_context`` / the two clip
transcoders / ``LANGUAGE_NAMES`` / ``WEBCALL_TTS_SAMPLE_RATE`` via
``adapters.language_runtime`` (§3.1 bridge 1), and
``SUPPORTED_OUTPUT_TELEPHONY_HANDLERS`` from the voice registry (module-internal):
monkeypatch string paths target
``voiceai.modules.voice.session.language.handoff.<name>``.

Five compile-time name-mangling accommodations inside otherwise-verbatim bodies
(the B5-B8 precedent): ``self.__handoff_text_for`` (×2), ``self.__handoff_mulaw_wire``
(×2 across the two callers), ``self.__enqueue_chunk``, ``self.__record_lid_event``
(×3) and ``self.__handoff_clip_convert`` are spelled ``self._TaskManager__<name>``,
which is exactly what the class body always compiled to. Signatures gained type
annotations (rule 6), public functions kept/gained Google docstrings (rule 7), and
the module logs through ``otobaai`` (rule 3; log content preserved).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.enums import LogComponent, LogDirection
from voiceai.modules.voice.adapters.language_runtime import (
    LANGUAGE_NAMES,
    WEBCALL_TTS_SAMPLE_RATE,
    SynthesizerPool,
    audio_to_mulaw8k,
    audio_to_pcm,
    convert_to_request_log,
    create_ws_data_packet,
    update_prompt_with_context,
)
from voiceai.modules.voice.constants import MODULE_NAME
from voiceai.modules.voice.registry import SUPPORTED_OUTPUT_TELEPHONY_HANDLERS

logger = get_logger(MODULE_NAME)

# Handoff clips are static per (voice, text): cache across calls so an N-language agent
# doesn't pay N TTS renders per call, concurrent with the welcome message. Process-wide.
HANDOFF_CLIP_CACHE: dict = {}
HANDOFF_CLIP_CACHE_MAX = 256

# The adapter-bound globals are re-exported on purpose: THIS module is the moved
# bodies' lookup site (R3), so tests and monkeypatches address them here.
__all__ = [
    "HANDOFF_CLIP_CACHE",
    "HANDOFF_CLIP_CACHE_MAX",
    "HandoffSession",
    "LANGUAGE_NAMES",
    "SUPPORTED_OUTPUT_TELEPHONY_HANDLERS",
    "SynthesizerPool",
    "WEBCALL_TTS_SAMPLE_RATE",
    "audio_to_mulaw8k",
    "audio_to_pcm",
    "convert_to_request_log",
    "create_ws_data_packet",
    "handoff_clip_convert",
    "handoff_mulaw_wire",
    "handoff_text_for",
    "play_switch_handoff",
    "prewarm_handoff_clips",
    "update_prompt_with_context",
]


class HandoffSession(Protocol):
    """The narrow facade of the live call session the handoff bodies drive.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in;
    it is never imported here) and by the handoff test doubles. The
    ``_TaskManager__*`` members are the session's own private methods reached back
    through their mangled names, so a ``patch.object(TaskManager, ...)`` or an
    instance-attr rebind intercepts internal dispatch too.
    """

    # --- teardown guards + per-call handoff state ---
    hangup_triggered: bool
    conversation_ended: bool
    handoff_audio_cache: dict
    switch_handoff_messages: dict
    context_data: Any  # why: legacy free-form context mapping or None
    synthesizer_provider: Any  # why: legacy provider label attr
    run_id: Any  # why: legacy run identifier

    # --- collaborators ---
    tools: dict
    conversation_history: Any  # why: legacy ConversationHistory

    # --- legacy session methods the bodies call back into ---
    def _get_voice_name_for_label(self, label: Any) -> Any: ...  # noqa: D102
    async def _synthesize(self, message: Any) -> Any: ...  # noqa: D102
    def _TaskManager__handoff_text_for(self, label: Any) -> Any: ...  # noqa: D102
    def _TaskManager__handoff_mulaw_wire(self) -> bool: ...  # noqa: D102
    def _TaskManager__handoff_clip_convert(self, synth: Any, audio: Any, mulaw_wire: Any) -> Any: ...  # noqa: D102
    def _TaskManager__enqueue_chunk(self, chunk: Any, i: int, number_of_chunks: int, meta_info: Any) -> Any: ...  # noqa: D102
    def _TaskManager__record_lid_event(self, record: dict) -> None: ...  # noqa: D102


async def play_switch_handoff(self: HandoffSession, target: str) -> None:
    """Speak the target language's handoff line (new voice, sequence_id=-1) to cover
    the post-switch reply-generation gap."""
    # Don't synthesize into a tearing-down pipeline (would truncate the goodbye).
    if self.hangup_triggered or self.conversation_ended:
        return
    handoff_text = self._TaskManager__handoff_text_for(target)
    if not handoff_text:
        return

    handoff_meta = {
        "io": self.tools["output"].get_provider(),
        "request_id": str(uuid.uuid4()),
        "sequence_id": -1,
        "message_category": "handoff",
        "text": handoff_text,
        "type": "audio",
    }
    clip = self.handoff_audio_cache.get(target)
    if clip:
        # Pre-warmed wire-format clip pushed straight to the transport. Both end-flags required
        handoff_meta.update(
            {
                "format": "mulaw" if self._TaskManager__handoff_mulaw_wire() else "pcm",
                "end_of_llm_stream": True,
                "end_of_synthesizer_stream": True,
                "is_first_chunk": True,
            }
        )
        # Cached synth logs — else the spoken handoff is invisible in the run log.
        for direction in (LogDirection.REQUEST, LogDirection.RESPONSE):
            convert_to_request_log(
                message=handoff_text,
                meta_info=handoff_meta,
                component=LogComponent.SYNTHESIZER,
                direction=direction,
                model=self.synthesizer_provider,
                is_cached=True,
                engine=self.tools["synthesizer"].get_engine(),
                run_id=self.run_id,
            )
        self._TaskManager__enqueue_chunk(clip, 0, 1, handoff_meta)
        self.conversation_history.append_assistant(handoff_text, sequence_id=-1, message_category="handoff")
        self._TaskManager__record_lid_event({"type": "handoff", "source": "prewarmed", "target": target})
        logger.info(f"LanguageSwitcher: playing pre-warmed handoff clip: {handoff_text!r}")
        return

    # Cold cache → live synthesis on the target voice (socket already warm).
    handoff_meta.update({"cached": False, "format": "pcm", "end_of_llm_stream": True})
    await self._synthesize(create_ws_data_packet(handoff_text, meta_info=handoff_meta))
    self.conversation_history.append_assistant(handoff_text, sequence_id=-1, message_category="handoff")
    self._TaskManager__record_lid_event({"type": "handoff", "source": "live", "target": target})
    logger.info(f"LanguageSwitcher: playing handoff to cover reply generation: {handoff_text!r}")


def handoff_text_for(self: HandoffSession, label: Any) -> Any:  # why: legacy label / rendered str contract
    """Render the configured handoff template for a language label ("" when none)."""
    template = self.switch_handoff_messages.get(label, "")
    if not template:
        return ""
    text = template.replace("{agent_name}", self._get_voice_name_for_label(label)).replace(
        "{language}", LANGUAGE_NAMES.get(label, label)
    )
    # As in the legacy handoff: render after the runtime placeholders so {customer_name} resolves.
    return update_prompt_with_context(text, self.context_data)


def handoff_mulaw_wire(self: HandoffSession) -> bool:
    """True on telephony (mu-law@8k clip), False on web/freeswitch (raw PCM@24k)."""
    return self.tools["output"].get_provider() in SUPPORTED_OUTPUT_TELEPHONY_HANDLERS


async def prewarm_handoff_clips(self: HandoffSession) -> None:
    """Pre-render each language's handoff on its own voice via one-shot synthesize().
    Clips are static for the call; non-active labels first (likely first targets)."""
    pool = self.tools.get("synthesizer")
    if not isinstance(pool, SynthesizerPool):
        return

    # A call has exactly one transport, so render in its wire format only.
    mulaw_wire = self._TaskManager__handoff_mulaw_wire()

    async def render(label: Any, synth: Any) -> None:  # why: legacy label / synthesizer duck types
        text = self._TaskManager__handoff_text_for(label)
        if not text:
            return
        cache_key = (
            synth.__class__.__name__,
            getattr(synth, "voice_id", None) or getattr(synth, "voice", None),
            text,
            "mulaw" if mulaw_wire else f"pcm{WEBCALL_TTS_SAMPLE_RATE}",
        )
        cached = HANDOFF_CLIP_CACHE.get(cache_key)
        if cached:
            self.handoff_audio_cache[label] = cached
            return
        # Under ~50ms is a failed one-shot returning a sentinel, not a clip.
        min_clip_bytes = 400 if mulaw_wire else 2400
        try:
            clip = None
            # Prefer a native one-shot in the wire format when the provider offers it.
            if mulaw_wire:
                telephony_one_shot = getattr(synth, "synthesize_telephony_clip", None)
                if telephony_one_shot is not None:
                    clip = await telephony_one_shot(text)
            else:
                pcm_one_shot = getattr(synth, "synthesize_pcm_clip", None)
                if pcm_one_shot is not None:
                    clip = await pcm_one_shot(text, WEBCALL_TTS_SAMPLE_RATE)
            # Before the fallback, so synthesize() still gets its turn.
            if clip and len(clip) < min_clip_bytes:
                logger.warning(
                    f"LanguageSwitcher: one-shot for '{label}' returned {len(clip)}B — falling back to synthesize()"
                )
                clip = None
            if not clip:
                audio = await synth.synthesize(text)
                if not audio:
                    return
                # pydub decode shells out to ffprobe/ffmpeg — keep it off the event loop.
                clip = await asyncio.to_thread(self._TaskManager__handoff_clip_convert, synth, audio, mulaw_wire)
            if clip and len(clip) < min_clip_bytes:
                logger.error(f"LanguageSwitcher: handoff clip for '{label}' is {len(clip)}B — discarding")
                return
            if clip:
                self.handoff_audio_cache[label] = clip
                if len(HANDOFF_CLIP_CACHE) >= HANDOFF_CLIP_CACHE_MAX:
                    HANDOFF_CLIP_CACHE.pop(next(iter(HANDOFF_CLIP_CACHE)))
                HANDOFF_CLIP_CACHE[cache_key] = clip
                synth.synthesized_characters = getattr(synth, "synthesized_characters", 0) + len(text)
                logger.info(f"LanguageSwitcher: pre-warmed handoff clip '{label}' ({len(clip)} bytes)")
        except Exception as e:
            logger.error(f"LanguageSwitcher: handoff prewarm failed for '{label}': {e}")

    await asyncio.gather(*(render(label, synth) for label, synth in pool.synthesizers.items()))


def handoff_clip_convert(self: HandoffSession, synth: Any, audio: Any, mulaw_wire: Any) -> Any:
    """Decode a one-shot render into the wire format. Blocking — run off-loop."""
    kwargs = {
        "rate_hint": getattr(synth, "sampling_rate", 0) or getattr(synth, "sample_rate", 0) or 8000,
        "format_hint": getattr(synth, "format", "") or "",
    }
    if mulaw_wire:
        clip = audio_to_mulaw8k(audio, **kwargs)
    else:
        clip = audio_to_pcm(audio, target_sample_rate=WEBCALL_TTS_SAMPLE_RATE, **kwargs)
    if clip is None:
        logger.error(
            f"LanguageSwitcher: handoff clip for {synth.__class__.__name__} is a compressed container "
            f"pydub can't decode into {'mulaw' if mulaw_wire else 'pcm'} — skipping"
        )
    return clip
