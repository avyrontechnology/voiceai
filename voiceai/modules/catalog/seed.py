"""Curated provider/model seed for the catalog (spec 0022, slice 1).

Curation rules (review to extend — never scrape):

- Every engine registry provider key appears at least once; model strings are
  the provider files' own defaults (constructor signatures) or values
  referenced in-repo (tests, pipelines). Nothing here is invented.
- `models_open=True` (suggestions only, validation accepts any non-empty
  string) for vendor-extended namespaces: LiteLLM-routed LLMs, Azure
  deployments (custom names), Deepgram, self-hosted (ollama/vllm/custom).
  Closed otherwise: wrong strings break or mis-bill, so review gates additions.
- `languages` are dropdown SUGGESTIONS (validation accepts any well-formed
  BCP-47 — codes are a standard, not provider folklore). Seeded from in-repo
  evidence where present (Maya's list verbatim), else the product's working
  pair `en`/`hi`, widened by review.
- Voices land in slice 3 with sample URLs — except Sarvam/Maya, whose closed
  speaker sets are already pinned in-repo (failing to list them would
  false-reject valid configs today).
"""

from __future__ import annotations

from typing import TypedDict

from voiceai.modules.catalog.constants import CATALOG_VERSION
from voiceai.modules.catalog.models import CatalogEntry, CatalogVoice

__all__ = ["SEED_ENTRIES", "seed_entries"]


def _entry(
    modality: str,
    provider: str,
    model: str,
    languages: list[str] | None = None,
    voices: list[CatalogVoice] | None = None,
    models_open: bool = False,
    voices_open: bool = False,
) -> CatalogEntry:
    """Build one catalog row with the v1 seed version stamped."""
    return CatalogEntry(
        catalog_id=f"{modality}:{provider}:{model}",
        modality=modality,  # type: ignore[arg-type]  # why: same table-literal discipline
        provider=provider,
        model=model,
        languages=languages or ["en"],
        voices=voices or [],
        models_open=models_open,
        voices_open=voices_open,
        catalog_version=CATALOG_VERSION,
    )


def _voice(name: str, language: str = "hi") -> CatalogVoice:
    """Build a sample-less voice row (slice 3 fills samples + genders)."""
    return CatalogVoice(name=name, language=language)


def _bulbul_speakers() -> list[CatalogVoice]:
    """Sarvam's closed speaker set, pinned by the synthesizer itself."""
    return [_voice(name) for name in ("anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh")]


def _maya_voices() -> list[CatalogVoice]:
    """Maya's closed voice set with its verbatim language list."""
    return [
        CatalogVoice(name="Ananya", language="hi"),
        CatalogVoice(name="Arjun", language="hi"),
    ]


_MAYA_LANGUAGES: list[str] = ["hi", "bn", "gu", "kn", "ml", "mr", "or", "pa", "ta", "te", "en", "auto"]

#: One table row's options (all optional; the table below stays readable).
class _RowOptions(TypedDict, total=False):
    """Seed-row options beyond the (modality, provider, model) key."""

    languages: list[str]
    voices: list[CatalogVoice]
    models_open: bool
    voices_open: bool


#: The curated v1 rows. Each tuple is (modality, provider, model, options).
_SEED_TABLE: tuple[tuple[str, str, str, _RowOptions], ...] = (
    # --- ASR: defaults are the providers' own constructor signatures ------------
    ("asr", "deepgram", "nova-3", {"models_open": True, "languages": ["en", "hi"]}),
    ("asr", "deepgram", "nova-2", {"models_open": True, "languages": ["en", "hi"]}),
    ("asr", "azure", "default", {"models_open": True, "languages": ["en-US", "hi-IN"]}),
    ("asr", "sarvam", "saaras:v3", {"languages": ["en", "hi"]}),
    ("asr", "sarvam", "saaras:v4", {"languages": ["en", "hi"]}),
    ("asr", "assembly", "universal-streaming", {"models_open": True}),
    ("asr", "google", "latest_long", {"models_open": True}),
    ("asr", "pixa", "pixa-1", {}),
    ("asr", "pixa", "whisper-1", {}),
    ("asr", "gladia", "default", {"models_open": True}),
    ("asr", "elevenlabs", "scribe_v2_realtime", {"models_open": True}),
    ("asr", "smallest", "pulse", {"models_open": True, "languages": ["en", "hi"]}),
    ("asr", "openai", "gpt-realtime-whisper", {"models_open": True}),
    ("asr", "openai", "whisper-1", {"models_open": True}),
    ("asr", "soniox", "stt-rt-v5", {"models_open": True}),
    ("asr", "gemini", "gemini-3.5-transcribe-live", {"models_open": True}),
    # --- TTS -------------------------------------------------------------------
    ("tts", "polly", "standard", {"models_open": False}),
    ("tts", "polly", "neural", {"models_open": False}),
    ("tts", "elevenlabs", "eleven_turbo_v2_5", {"models_open": True, "voices_open": True}),
    ("tts", "openai", "tts-1", {"models_open": True}),
    ("tts", "deepgram", "aura-zeus-en", {"models_open": True}),
    ("tts", "azuretts", "neural", {"models_open": True}),
    ("tts", "cartesia", "sonic-english", {"models_open": True}),
    ("tts", "smallest", "lightning_v3.1", {"models_open": True, "languages": ["en", "hi"]}),
    ("tts", "sarvam", "bulbul:v2", {"languages": ["en", "hi"], "voices": _bulbul_speakers()}),
    ("tts", "sarvam", "bulbul:v3", {"languages": ["en", "hi"], "voices": _bulbul_speakers(), "voices_open": True}),
    ("tts", "rime", "arcana", {"models_open": True}),
    ("tts", "rime", "mistv2", {"models_open": True}),
    ("tts", "pixa", "luna-tts", {}),
    ("tts", "maya", "Maya 2 Native", {"languages": _MAYA_LANGUAGES, "voices": _maya_voices()}),
    ("tts", "kalpa", "kalpa-tts-multilingual-beta-v0.1", {"models_open": True, "voices": [_voice("Kiara")]}),
    # --- S2S: closed realtime sets ----------------------------------------------
    ("s2s", "openai_realtime", "gpt-realtime-2.1", {}),
    ("s2s", "openai_realtime", "gpt-realtime-2.1-mini", {}),
    ("s2s", "openai_realtime", "gpt-realtime-2", {}),
    ("s2s", "gemini_live", "gemini-3.1-flash-live-preview", {}),
    # --- LLM: pinned first-party lists; LiteLLM-routed namespaces stay open ----
    ("llm", "openai", "gpt-4o", {}),
    ("llm", "openai", "gpt-4o-mini", {}),
    ("llm", "openai", "gpt-4.1-mini", {}),
    ("llm", "openai", "gpt-3.5-turbo", {}),
    ("llm", "google", "gemini-3.6-flash", {}),
    ("llm", "google", "gemini-3", {}),
    ("llm", "groq", "llama-3.3-70b-versatile", {"models_open": True}),
    ("llm", "anthropic", "claude-sonnet-4-5", {"models_open": True}),
    ("llm", "cohere", "command-r", {"models_open": True}),
    ("llm", "deepinfra", "meta-llama/Meta-Llama-3.1-70B-Instruct", {"models_open": True}),
    ("llm", "together", "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", {"models_open": True}),
    ("llm", "fireworks", "accounts/fireworks/models/llama-v3p1-70b-instruct", {"models_open": True}),
    ("llm", "azure-openai", "default", {"models_open": True}),
    ("llm", "perplexity", "llama-3.1-sonar-large-128k-online", {"models_open": True}),
    ("llm", "vllm", "default", {"models_open": True}),
    ("llm", "anyscale", "meta-llama/Meta-Llama-3.1-70B-Instruct", {"models_open": True}),
    ("llm", "custom", "default", {"models_open": True}),
    ("llm", "ola", "default", {"models_open": True}),
    ("llm", "deepseek", "deepseek-chat", {"models_open": True}),
    ("llm", "openrouter", "openai/gpt-4o-mini", {"models_open": True}),
    ("llm", "azure", "default", {"models_open": True}),
    ("llm", "ollama", "llama3.1", {"models_open": True}),
)


def _build_entry(modality: str, provider: str, model: str, options: _RowOptions) -> CatalogEntry:
    """Render one table row."""
    return _entry(
        modality,
        provider,
        model,
        languages=list(options.get("languages", ["en"])),
        voices=list(options.get("voices", [])),
        models_open=bool(options.get("models_open", False)),
        voices_open=bool(options.get("voices_open", False)),
    )


#: Materialized seed rows (built once; the loader inserts them idempotently).
SEED_ENTRIES: tuple[CatalogEntry, ...] = tuple(
    _build_entry(modality, provider, model, options) for modality, provider, model, options in _SEED_TABLE
)


def seed_entries() -> list[CatalogEntry]:
    """Return fresh copies of the seed rows for the loader."""
    return [entry.model_copy(deep=True) for entry in SEED_ENTRIES]
