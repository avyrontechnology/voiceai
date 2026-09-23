"""The relocated transcribers (spec 0004, B12c): shim identity + deepgram split pins.

Three contracts under test, the B5 ``test_s2s_runner`` shim-identity / B12a-b
``test_relocation`` precedent. First, every legacy ``voiceai.transcriber`` path is a
pure identity shim over the relocated ``voiceai.modules.voice.asr`` tree — the class
objects are IDENTICAL (never copies). Second, the deepgram 4-way split holds: the
connection/nova/flux bodies live in their session modules with
``DeepgramTranscriber`` keeping the class name, import path and thin same-named
methods, and the B1 golden fixtures plus the flux/turn-finalization/stuck-turn
suites validate the split. Third, the new tree owns the moved bodies' lookup sites
(the ``asr_runtime`` bridge) and the pool resolves by identity for the
``isinstance`` gates.
"""

import voiceai.transcriber as legacy_pkg
import voiceai.modules.voice.asr.providers as new_pkg
import voiceai.transcriber.base_transcriber as legacy_base
import voiceai.modules.voice.asr.base as new_base
import voiceai.transcriber.transcriber_pool as legacy_pool
import voiceai.modules.voice.asr.pool as new_pool
import voiceai.transcriber.deepgram_transcriber as legacy_dg
import voiceai.modules.voice.asr.providers.deepgram.transcriber as new_dg
import voiceai.modules.voice.asr.providers.deepgram.connection as new_dg_conn
import voiceai.modules.voice.asr.providers.deepgram.nova_session as new_dg_nova
import voiceai.modules.voice.asr.providers.deepgram.flux_session as new_dg_flux
import voiceai.transcriber.sarvam_transcriber as legacy_sarvam
import voiceai.modules.voice.asr.providers.sarvam_transcriber as new_sarvam


def test_provider_shims_are_identity_reexports():
    assert legacy_base.BaseTranscriber is new_base.BaseTranscriber
    assert legacy_pool.TranscriberPool is new_pool.TranscriberPool
    assert legacy_dg.DeepgramTranscriber is new_dg.DeepgramTranscriber
    assert legacy_sarvam.SarvamTranscriber is new_sarvam.SarvamTranscriber
    assert legacy_pkg.DeepgramTranscriber is new_pkg.DeepgramTranscriber
    assert legacy_pkg.TranscriberPool is new_pkg.TranscriberPool


def test_deepgram_split_holds_the_session_seams():
    for name in (
        "get_deepgram_ws_url",
        "send_heartbeat",
        "toggle_connection",
        "cleanup",
        "deepgram_connect",
    ):
        assert callable(getattr(new_dg_conn, name)), f"missing connection.{name}"
        assert callable(getattr(new_dg.DeepgramTranscriber, name)), f"missing delegator {name}"
    for name in (
        "reset_turn_state",
        "force_finalize_utterance",
        "monitor_utterance_timeout",
        "sender",
        "sender_stream",
        "receiver",
    ):
        assert callable(getattr(new_dg_nova, name)), f"missing nova.{name}"
    for name in (
        "flux_turn_is_stalled",
        "release_stuck_flux_turn",
        "monitor_flux_turn_timeout",
        "receiver_flux",
    ):
        assert callable(getattr(new_dg_flux, name)), f"missing flux.{name}"


def test_pool_class_survives_for_the_isinstance_gates():
    from voiceai.modules.voice.asr.pool import TranscriberPool

    assert legacy_pool.TranscriberPool is TranscriberPool
