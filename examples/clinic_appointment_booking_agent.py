"""Clinic appointment-booking agent: Sarvam STT/TTS (shubh) + Gemini LLM.

Run:
    SARVAM_API_KEY=... GOOGLE_API_KEY=... python examples/clinic_appointment_booking_agent.py
    # or GEMINI_API_KEY instead of GOOGLE_API_KEY (both accepted)

Test via local server:
    POST /agent with {"agent_config": build_agent_config(), "agent_prompts": build_agent_prompts()}
    then connect ws://localhost:5001/chat/v1/<agent_id>
"""

import asyncio
from typing import Any, Dict

from voiceai.assistant import Assistant
from voiceai.models import AgentModel, LlmAgent, SimpleLlmAgent, Synthesizer, SarvamConfig, Transcriber

BOOKING_TOOL_NAME = "book_appointment"

# Sarvam speaker names are lowercase; "Shubh" is a 400. shubh is the bulbul:v3 default
# and the recommended male voice for hi-IN/te-IN/kn-IN/od-IN/ml-IN.
SARVAM_TTS_VOICE = "shubh"
SARVAM_TTS_MODEL = "bulbul:v3"
SARVAM_TTS_LANGUAGE = "en-IN"
SARVAM_STT_MODEL = "saaras:v3"
SARVAM_STT_LANGUAGE = "en-IN"

GEMINI_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "You are a polite clinic receptionist booking appointments. "
    "Collect patient_name, phone_number, department or doctor, preferred_date and preferred_time. "
    "If the caller is vague, ask one short follow-up at a time. "
    "Once all slots are filled, repeat the details back to confirm. "
    "Only after the caller confirms, call book_appointment and then speak the booking confirmation. "
    "Keep replies short, warm and natural. Never invent a booking id."
)

WELCOME_MESSAGE = "Hello! Thanks for calling our clinic. How can I help you with your appointment today?"

BOOKING_TOOL = {
    "type": "function",
    "function": {
        "name": BOOKING_TOOL_NAME,
        "description": "Book a clinic appointment once the caller has confirmed all details.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_name": {"type": "string", "description": "Full name of the patient."},
                "phone_number": {"type": "string", "description": "Callback phone number."},
                "department": {"type": "string", "description": "Department or doctor, e.g. general, dental."},
                "preferred_date": {"type": "string", "description": "Preferred date, e.g. 2026-09-10."},
                "preferred_time": {"type": "string", "description": "Preferred time, e.g. 10:30 AM."},
                "reason_for_visit": {"type": "string", "description": "Brief reason for the visit."},
            },
            "required": ["patient_name", "preferred_date", "preferred_time"],
        },
    },
}


def build_agent_config() -> Dict[str, Any]:
    """AgentModel-ready dict using Sarvam STT/TTS and Gemini LLM."""
    return {
        "agent_name": "clinic_appointment_agent",
        "agent_type": "other",
        "agent_welcome_message": WELCOME_MESSAGE,
        "tasks": [
            {
                "task_type": "conversation",
                "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm", "synthesizer"]]},
                "tools_config": {
                    "input": {"provider": "default", "format": "wav"},
                    "output": {"provider": "default", "format": "wav"},
                    "transcriber": {
                        "provider": "sarvam",
                        "model": SARVAM_STT_MODEL,
                        "language": SARVAM_STT_LANGUAGE,
                        "stream": True,
                        "sampling_rate": 16000,
                        "encoding": "linear16",
                    },
                    "llm_agent": {
                        "agent_type": "simple_llm_agent",
                        "agent_flow_type": "streaming",
                        "llm_config": {
                            "provider": "google",
                            "model": GEMINI_MODEL,
                            "temperature": 0.2,
                            "max_tokens": 150,
                        },
                    },
                    "synthesizer": {
                        "provider": "sarvam",
                        "provider_config": {
                            "voice": SARVAM_TTS_VOICE,
                            "voice_id": SARVAM_TTS_VOICE,
                            "model": SARVAM_TTS_MODEL,
                            "language": SARVAM_TTS_LANGUAGE,
                            "speed": 1.0,
                        },
                        "stream": True,
                        "audio_format": "pcm",
                        "buffer_size": 40,
                    },
                    "api_tools": {
                        "tools": [BOOKING_TOOL],
                        "tools_params": {
                            BOOKING_TOOL_NAME: {
                                # Point this at your scheduling API; without a URL the tool still
                                # validates the LLM call shape and the turn is logged.
                                "url": None,
                                "method": "POST",
                                "pre_call_message": "One moment while I book that for you.",
                            }
                        },
                    },
                },
                "task_config": {"hangup_after_silence": 20, "check_if_user_online": True},
            }
        ],
    }


def build_agent_prompts() -> Dict[str, Dict[str, str]]:
    return {"task_1": {"system_prompt": SYSTEM_PROMPT, "welcome_message": WELCOME_MESSAGE}}


async def main() -> None:
    # Validates the wiring before any audio flows.
    AgentModel(**build_agent_config())

    assistant = Assistant(name="clinic_appointment_agent")
    assistant.add_task(
        task_type="conversation",
        llm_agent=LlmAgent(
            agent_type="simple_llm_agent",
            agent_flow_type="streaming",
            llm_config=SimpleLlmAgent(provider="google", model=GEMINI_MODEL, temperature=0.2, max_tokens=150),
        ),
        transcriber=Transcriber(
            provider="sarvam", model=SARVAM_STT_MODEL, language=SARVAM_STT_LANGUAGE, stream=True
        ),
        synthesizer=Synthesizer(
            provider="sarvam",
            provider_config=SarvamConfig(
                voice=SARVAM_TTS_VOICE, voice_id=SARVAM_TTS_VOICE, model=SARVAM_TTS_MODEL, language=SARVAM_TTS_LANGUAGE
            ),
            stream=True,
            audio_format="pcm",
        ),
        enable_textual_input=True,
    )
    # Attach the booking tool to the in-process run (server path carries it in agent_config).
    assistant.tasks[0]["tools_config"]["api_tools"] = {
        "tools": [BOOKING_TOOL],
        "tools_params": {BOOKING_TOOL_NAME: {"url": None, "method": "POST"}},
    }
    async for chunk in assistant.execute():
        print(chunk)


if __name__ == "__main__":
    asyncio.run(main())
