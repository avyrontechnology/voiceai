"""Clinic appointment-booking agent: Sarvam STT/TTS (shubh) + Gemini LLM.

Pins the provider wiring the user asked to test so a regression (wrong
provider, wrong voice casing, missing booking tool) fails fast.
"""

from voiceai.models import AgentModel


def _load_builder():
    from examples.clinic_appointment_booking_agent import (
        BOOKING_TOOL_NAME,
        build_agent_config,
        build_agent_prompts,
    )

    return BOOKING_TOOL_NAME, build_agent_config, build_agent_prompts


def test_clinic_agent_uses_sarvam_stt_tts_and_gemini_llm():
    _, build_agent_config, _ = _load_builder()
    config = build_agent_config()

    # Pydantic validation catches malformed provider blocks before a call starts.
    agent = AgentModel(**config)
    task = agent.tasks[0]
    tools = task.tools_config

    assert tools.transcriber.provider == "sarvam"
    assert tools.transcriber.model == "saaras:v3"
    assert tools.transcriber.stream is True

    assert tools.synthesizer.provider == "sarvam"
    synth_cfg = tools.synthesizer.provider_config
    # Sarvam speaker names are lowercase; "Shubh" is a 400.
    assert synth_cfg.voice_id == "shubh"
    assert synth_cfg.voice == "shubh"
    assert synth_cfg.model == "bulbul:v3"

    llm_cfg = tools.llm_agent["llm_config"] if isinstance(tools.llm_agent, dict) else tools.llm_agent.llm_config
    provider = llm_cfg["provider"] if isinstance(llm_cfg, dict) else llm_cfg.provider
    model = llm_cfg["model"] if isinstance(llm_cfg, dict) else llm_cfg.model
    assert provider == "google"
    assert "gemini" in model


def test_clinic_agent_exposes_booking_tool_with_required_slots():
    tool_name, build_agent_config, _ = _load_builder()
    config = build_agent_config()
    agent = AgentModel(**config)
    api_tools = agent.tasks[0].tools_config.api_tools

    names = []
    for tool in api_tools.tools:
        spec = tool.function if hasattr(tool, "function") else tool
        names.append(spec.name if hasattr(spec, "name") else spec["name"])
    assert tool_name in names

    for tool in api_tools.tools:
        spec = tool.function if hasattr(tool, "function") else tool
        name = spec.name if hasattr(spec, "name") else spec["name"]
        if name != tool_name:
            continue
        params = spec.parameters if hasattr(spec, "parameters") else spec["parameters"]
        for slot in ("patient_name", "phone_number", "preferred_date", "preferred_time"):
            assert slot in params["properties"]
        for slot in ("patient_name", "preferred_date", "preferred_time"):
            assert slot in params["required"]


def test_clinic_agent_prompts_cover_booking_flow():
    _, _, build_agent_prompts = _load_builder()
    prompts = build_agent_prompts()
    system_prompt = prompts["task_1"]["system_prompt"].lower()
    assert "appointment" in system_prompt
    assert "confirm" in system_prompt
