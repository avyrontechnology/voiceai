"""The relocated synthesizers (spec 0004, B12b): shim identity + kalpa split pins.

Three contracts under test, the B5 ``test_s2s_runner`` shim-identity / B12a
``test_relocation`` precedent. First, every legacy ``voiceai.synthesizer`` path is a
pure identity shim over the relocated ``voiceai.modules.voice.tts`` tree — the class
objects are IDENTICAL (never copies), including the kalpa/sarvam/maya constants the
provider suites import. Second, the kalpa split holds: the HTTP one-shot bodies live
in ``tts.providers.kalpa_http`` with ``KalpaSynthesizer`` keeping thin same-named
methods, and ``kalpa_http`` is the lookup site for ``aiohttp`` (the one-shot suites
patch it there — R3) while ``RESPONSE_IDLE_TIMEOUT``/``websockets`` stay looked up
in ``kalpa_synthesizer``. Third, the new tree owns the moved bodies' lookup sites
(the ``tts_runtime`` bridge) and the pools resolve by identity for the
``isinstance`` gates.
"""

import voiceai.synthesizer as legacy_pkg
import voiceai.modules.voice.tts.providers as new_pkg
import voiceai.synthesizer.base_synthesizer as legacy_base
import voiceai.modules.voice.tts.base as new_base
import voiceai.synthesizer.synthesizer_pool as legacy_pool
import voiceai.modules.voice.tts.pool as new_pool
import voiceai.synthesizer.stream_synthesizer as legacy_stream
import voiceai.modules.voice.tts.stream as new_stream
import voiceai.synthesizer.kalpa_synthesizer as legacy_kalpa
import voiceai.modules.voice.tts.providers.kalpa_synthesizer as new_kalpa
import voiceai.modules.voice.tts.providers.kalpa_http as new_kalpa_http
import voiceai.synthesizer.sarvam_synthesizer as legacy_sarvam
import voiceai.modules.voice.tts.providers.sarvam_synthesizer as new_sarvam
import voiceai.synthesizer.maya_synthesizer as legacy_maya
import voiceai.modules.voice.tts.providers.maya_synthesizer as new_maya


def test_provider_shims_are_identity_reexports():
    assert legacy_base.BaseSynthesizer is new_base.BaseSynthesizer
    assert legacy_pool.SynthesizerPool is new_pool.SynthesizerPool
    assert legacy_stream.StreamSynthesizer is new_stream.StreamSynthesizer
    assert legacy_kalpa.KalpaSynthesizer is new_kalpa.KalpaSynthesizer
    assert legacy_kalpa._VOICE_IDS is new_kalpa._VOICE_IDS
    assert legacy_kalpa.KALPA_NATIVE_SAMPLE_RATE is new_kalpa.KALPA_NATIVE_SAMPLE_RATE
    assert legacy_sarvam.SarvamSynthesizer is new_sarvam.SarvamSynthesizer
    assert legacy_maya.MayaSynthesizer is new_maya.MayaSynthesizer
    assert legacy_pkg.KalpaSynthesizer is new_pkg.KalpaSynthesizer
    assert legacy_pkg.SynthesizerPool is new_pkg.SynthesizerPool


def test_kalpa_http_split_holds_the_one_shot_path():
    for name in (
        "generate_http",
        "synthesize",
        "synthesize_telephony_clip",
        "process_http_audio",
        "get_http_audio_format",
    ):
        assert callable(getattr(new_kalpa_http, name)), f"missing {name}"
    for name in (
        "_generate_http",
        "synthesize",
        "synthesize_telephony_clip",
        "_process_http_audio",
        "_get_http_audio_format",
    ):
        assert callable(getattr(new_kalpa.KalpaSynthesizer, name)), f"missing delegator {name}"


def test_kalpa_lookup_sites_are_split_by_reader():
    import aiohttp
    import websockets

    # getattr (not attribute access): the parity pin is the runtime re-export, and
    # no_implicit_reexport bans static access to merely-imported names.
    assert getattr(new_kalpa_http, "aiohttp") is aiohttp
    assert getattr(new_kalpa, "websockets") is websockets
    assert new_kalpa.RESPONSE_IDLE_TIMEOUT == 10.0


def test_pool_class_survives_for_the_isinstance_gates():
    from voiceai.modules.voice.tts.pool import SynthesizerPool

    assert legacy_pool.SynthesizerPool is SynthesizerPool
