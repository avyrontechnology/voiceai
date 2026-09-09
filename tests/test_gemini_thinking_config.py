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
    """Default is explicit minimal (no server-side medium/high), not None."""
    llm = _llm("gemini-3.6-flash")
    config = llm._get_thinking_config()
    assert config is not None
    assert config.thinking_level.value.lower() == "minimal"
    assert not config.include_thoughts


def test_gemini3_thinking_when_budget_requested():
    llm = _llm("gemini-3.6-flash", thinking_budget=512)
    config = llm._get_thinking_config()
    assert config is not None
    assert config.thinking_level.value.lower() == default_thinking_level("gemini-3.6-flash")


def test_gemini3_default_is_minimal_without_include_thoughts():
    """Unspecified thinking must NOT fall back to server-side medium/high.

    Gemini 3 docs: omitted thinking defaults to medium/high server-side
    (measured 46s TTFT with low+include_thoughts vs ~3s without). The voice
    default is explicit thinking_level="minimal" ("matches no-thinking")
    with no include_thoughts.
    """
    llm = _llm("gemini-3.6-flash")
    config = llm._get_thinking_config()
    assert config is not None
    assert config.thinking_level.value.lower() == "minimal"
    assert not config.include_thoughts
    # Never co-set level + budget (400 on some families, wasted tokens elsewhere).
    assert config.thinking_budget is None


def test_gemini3_budget_maps_to_level_never_coset():
    llm = _llm("gemini-3.6-flash", thinking_budget=512)
    config = llm._get_thinking_config()
    assert config is not None
    assert config.thinking_level.value.lower() == default_thinking_level("gemini-3.6-flash")
    assert config.thinking_budget is None


def test_gemini3_built_config_never_cosets_level_and_budget():
    """The built GenerateContentConfig must never carry both knobs at once."""
    for kwargs in ({}, {"thinking_budget": 512}):
        llm = _llm("gemini-3.6-flash", **kwargs)
        built = llm._build_config("sys")
        tc = built.thinking_config
        assert tc is not None
        assert not (tc.thinking_level is not None and tc.thinking_budget is not None), kwargs
    llm25 = _llm("gemini-2.5-flash", thinking_budget=512)
    built25 = llm25._build_config("sys")
    tc25 = built25.thinking_config
    assert tc25 is not None
    assert not (tc25.thinking_level is not None and tc25.thinking_budget is not None)


def test_thinking_model_max_output_tokens_floor():
    """Thinking eats the cap: agent max_output_tokens=150 starves replies.

    Thinking models clamp to >=512 (warn when clamping); larger caps pass through.
    """
    small = _llm("gemini-3.6-flash", max_tokens=150)
    assert small.max_tokens >= 512
    assert small._build_config("sys").max_output_tokens >= 512
    big = _llm("gemini-3.6-flash", max_tokens=1024)
    assert big.max_tokens == 1024
    assert big._build_config("sys").max_output_tokens == 1024


def test_thinking_25_max_output_tokens_floor():
    small = _llm("gemini-2.5-flash", max_tokens=100)
    assert small.max_tokens >= 512


def test_sdk_afc_always_disabled():
    """Manual post-stream dispatch is the orchestrator; SDK AFC must stay off.

    Covers both the tool and the no-tool case — the no-tool case previously
    left automatic_function_calling unset (SDK default on).
    """
    bare = _llm("gemini-3.6-flash")
    assert bare._build_config("sys").automatic_function_calling is not None
    assert bare._build_config("sys").automatic_function_calling.disable is True
    tooled = _llm(
        "gemini-3.6-flash",
        api_tools={
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}},
                }
            ],
            "tools_params": {},
        },
    )
    assert tooled._build_config("sys").automatic_function_calling.disable is True


def test_gemini_sdk_version_logged(caplog):
    """GeminiLLM init logs the pinned google-genai SDK version for drift triage."""
    import logging

    with caplog.at_level(logging.INFO):
        llm = _llm("gemini-3.6-flash")
    assert any("genai_sdk_version" in r.message or "genai_sdk_version" in str(r.args) for r in caplog.records), [
        r.message for r in caplog.records
    ]
    assert llm.genai_sdk_version != "unknown"


def test_google_default_model_is_gemini36():
    """LLM_DEFAULT_CONFIGS google must not point at retired gemini-2.5-flash."""
    from voiceai.constants import LLM_DEFAULT_CONFIGS

    assert LLM_DEFAULT_CONFIGS["google"]["model"] == "gemini-3.6-flash"


def test_gemini25_keeps_explicit_budget_behavior():
    assert _llm("gemini-2.5-flash", thinking_budget=512)._get_thinking_config() is not None
    disabled = _llm("gemini-2.5-flash")._get_thinking_config()
    assert disabled is not None and disabled.thinking_budget == 0
