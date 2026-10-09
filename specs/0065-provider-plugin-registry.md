# Spec 0065: plugin registry for ASR/TTS/telephony providers

## Goal

Adding a new provider today means editing hardcoded branches in the executor
and provider selection logic — an Open/Closed Principle violation. Introduce a
plugin registry so providers register themselves and the system resolves them
by name, never by modifying existing code.

## Non-goals

- No behavior change (existing provider selection unchanged)
- No new providers added in this spec
- No provider interface changes (existing protocols stay)
- Telephony provider registry (Twilio/Plivo) — separate spec

## Interface contracts

New module `voiceai/modules/voice/providers/`:

### `registry.py` — the plugin registry
- `register_asr_provider(name: str, factory: Callable[..., BaseTranscriber]) -> None`
- `register_tts_provider(name: str, factory: Callable[..., BaseSynthesizer]) -> None`
- `get_asr_provider(name: str) -> BaseTranscriber | None`
- `get_tts_provider(name: str) -> BaseSynthesizer | None`
- `list_asr_providers() -> list[str]`
- `list_tts_providers() -> list[str]`
- Thread-safe (asyncio.Lock)

### `base.py` — provider protocols
- `ASRProvider` (Protocol): `transcribe(audio: bytes) -> str`, `supports_streaming() -> bool`
- `TTSProvider` (Protocol): `synthesize(text: str) -> bytes`, `set_language(lang: str) -> None`

### `builtin.py` — built-in provider registration
- Registers all 11 ASR providers + 13 TTS providers at import time
- Called once from `voiceai/modules/voice/__init__.py`

## Data model

N/A — no persisted models.

## Security notes

N/A — no auth or secrets.

## Test plan

- `test_registry.py` — register, get, list, thread-safety
- `test_builtin_providers.py` — all built-in providers registered
- Existing provider tests pass unchanged

## Verification

```sh
make check
make sec
```

## Rollback

- Registry is additive — no existing code modified
- Rollback = revert

## Burn-down

- [ ] `providers/registry.py` — plugin registry
- [ ] `providers/base.py` — ASRProvider, TTSProvider protocols
- [ ] `providers/builtin.py` — built-in provider registration
- [ ] Wire into `voiceai/modules/voice/__init__.py`
- [ ] `test_registry.py` — registry tests
- [ ] `make check` green
