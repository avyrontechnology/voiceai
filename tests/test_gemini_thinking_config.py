"""Gemini 3.x must not force thinking on voice turns that never asked for it.

Regression: _get_thinking_config unconditionally attached
ThinkingConfig(thinking_level="low", include_thoughts=True) to every gemini-3
call. Measured live: gemini-3.6-flash TTFT 46.2s with that config vs 2.95s
without it (same prompt, same budget). A 46s first token means every LLM turn
is cancelled by the caller's next utterance first — the agent greets, then
stays silent forever despite working STT. Thinking must be opt-in via
thinking_budget, mirroring the 2.5 family.
"""

from voiceai.constants import default_thinking_level
from voiceai.llms.gemini_llm import GeminiLLM


def _llm(model, **kwargs):
    return GeminiLLM(model=model, llm_key="test-key", **kwargs)


def test_gemini3_no_thinking_by_default():
    llm = _llm("gemini-3.6-flash")
    assert llm._get_thinking_config() is None


def test_gemini3_thinking_when_budget_requested():
    llm = _llm("gemini-3.6-flash", thinking_budget=512)
    config = llm._get_thinking_config()
    assert config is not None
    assert config.thinking_level.value.lower() == default_thinking_level("gemini-3.6-flash")


def test_gemini25_keeps_explicit_budget_behavior():
    assert _llm("gemini-2.5-flash", thinking_budget=512)._get_thinking_config() is not None
    disabled = _llm("gemini-2.5-flash")._get_thinking_config()
    assert disabled is not None and disabled.thinking_budget == 0
