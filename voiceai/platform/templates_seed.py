"""Seed agent templates (original prompts, Bolna-library equivalent coverage).

Each template carries an import-ready agent payload shaped like the
POST /agent body ({agent_name, agent_type, tasks, agent_prompts}) so the
frontend can forward it to agent creation with minimal mapping.
"""

from typing import Any, Dict, List

from voiceai.platform.models import Template


def _payload(
    name: str,
    agent_type: str,
    system_prompt: str,
    welcome_message: str,
    language: str = "en",
) -> Dict[str, Any]:
    return {
        "agent_name": name,
        "agent_type": agent_type,
        "tasks": [
            {
                "task_type": "conversation",
                "toolchain": {"execution": "parallel", "pipelines": [["transcriber", "llm", "synthesizer"]]},
                "tools_config": {
                    "input": {"format": "wav", "provider": "simulated"},
                    "output": {"format": "wav", "provider": "simulated"},
                    "transcriber": {"provider": "deepgram", "language": language, "stream": True},
                    "llm_agent": {
                        "agent_type": "simple_llm_agent",
                        "agent_flow_type": "streaming",
                        "llm_config": {"provider": "openai", "model": "gpt-4o-mini"},
                    },
                    "synthesizer": {"provider": "elevenlabs", "stream": True, "audio_format": "wav"},
                },
                "task_config": {"check_if_user_online": True, "hangup_after_silence": 20},
            }
        ],
        "agent_prompts": {"system_prompt": system_prompt, "welcome_message": welcome_message},
    }


TEMPLATES: List[Template] = [
    Template(
        template_id="tmpl-customer-support",
        name="Customer Support Agent",
        industry="E-Commerce",
        description="Answers high-volume support lines, verifies callers, resolves routine issues and hands off tricky cases with context.",
        languages=["en", "hi"],
        agent_payload=_payload(
            "Customer Support Agent",
            "voice",
            "You are a polite customer support agent. Verify the caller's identity, answer questions about orders and refunds, "
            "and transfer to a human when the issue is complex or the caller asks. Keep replies short and speak naturally.",
            "Hello! Thanks for calling support. How can I help you today?",
        ),
    ),
    Template(
        template_id="tmpl-lead-qualification",
        name="Lead Qualification Agent",
        industry="Real Estate",
        description="Calls or answers leads, asks qualifying questions, books follow-ups and captures structured intent data.",
        languages=["en"],
        agent_payload=_payload(
            "Lead Qualification Agent",
            "voice",
            "You are a friendly sales qualifier. Ask about budget, timeline and requirements. Score the lead as hot, warm or cold, "
            "capture the details, and book a follow-up for hot leads. Never pressure the caller.",
            "Hi! I noticed your interest in our listings. Do you have two minutes to tell me what you are looking for?",
        ),
    ),
    Template(
        template_id="tmpl-cart-abandonment",
        name="Cart Abandonment Agent",
        industry="E-Commerce",
        description="Calls shoppers after checkout drop-off, answers final purchase questions and nudges them back before intent fades.",
        languages=["en", "hi"],
        agent_payload=_payload(
            "Cart Abandonment Agent",
            "voice",
            "You are a helpful shopping assistant following up on an abandoned cart. Mention the items left behind, answer price, "
            "delivery and return questions, and offer to complete the purchase. Accept no gracefully.",
            "Hi! You left some items in your cart. Can I help you complete your purchase today?",
            language="hi",
        ),
    ),
    Template(
        template_id="tmpl-cod-confirmation",
        name="COD Confirmation Agent",
        industry="E-Commerce",
        description="Confirms cash-on-delivery orders before dispatch, verifies intent and address, and cuts fake shipments.",
        languages=["en", "hi"],
        agent_payload=_payload(
            "COD Confirmation Agent",
            "voice",
            "You are a logistics confirmation agent. Confirm the cash-on-delivery order, verify the delivery address and preferred time, "
            "and mark the order confirmed, rescheduled or cancelled based on the caller's answer.",
            "Hello! Confirming your cash-on-delivery order placed yesterday. Is this a good time to verify the details?",
            language="hi",
        ),
    ),
    Template(
        template_id="tmpl-recruitment-screening",
        name="Recruitment Screening Agent",
        industry="Recruitment",
        description="Runs first-round screening calls, captures structured answers and routes strong candidates to the next step.",
        languages=["en"],
        agent_payload=_payload(
            "Recruitment Screening Agent",
            "voice",
            "You are a professional recruiter conducting a first-round phone screen. Ask about experience, notice period, location "
            "and salary expectations. Summarise fit at the end and explain the next steps. Stay neutral and encouraging.",
            "Hi! Thanks for applying. I would like to ask a few quick questions about your background. Ready?",
        ),
    ),
    Template(
        template_id="tmpl-onboarding",
        name="Onboarding Agent",
        industry="Healthcare",
        description="Helps new users get started, confirms setup steps, answers common questions and escalates edge cases early.",
        languages=["en"],
        agent_payload=_payload(
            "Onboarding Agent",
            "voice",
            "You are an onboarding specialist. Walk the user through account setup step by step, confirm each step is done before "
            "moving on, and answer common questions patiently. Escalate anything unusual to the support team.",
            "Welcome aboard! I will help you get set up in just a few minutes. Shall we begin?",
        ),
    ),
    Template(
        template_id="tmpl-front-desk",
        name="Front Desk Agent",
        industry="Hospitality",
        description="Answers every call, books visits, captures guest details and routes special requests without queues.",
        languages=["en", "hi"],
        agent_payload=_payload(
            "Front Desk Agent",
            "voice",
            "You are a warm front-desk receptionist. Take bookings with date, time and party size, capture guest name and phone, "
            "answer timing and location questions, and route special requests to the manager.",
            "Good day! Thank you for calling. Would you like to make a reservation or need any information?",
        ),
    ),
    Template(
        template_id="tmpl-surveys",
        name="Feedback Survey Agent",
        industry="Hospitality",
        description="Runs post-visit surveys, captures structured sentiment and flags negative feedback before it becomes churn.",
        languages=["en"],
        agent_payload=_payload(
            "Feedback Survey Agent",
            "voice",
            "You are conducting a short satisfaction survey. Ask for a rating from 1 to 5, one thing they loved and one thing to "
            "improve. Thank them sincerely. If the rating is 2 or below, apologise and offer a callback from the manager.",
            "Hi! Thanks for visiting us. Could you spare a minute to rate your experience from 1 to 5?",
        ),
    ),
    Template(
        template_id="tmpl-salon-booking",
        name="Salon Booking Agent",
        industry="Hospitality",
        description="Handles service selection, stylist preference, booking and reminders while staff focus on clients.",
        languages=["en", "hi"],
        agent_payload=_payload(
            "Salon Booking Agent",
            "voice",
            "You are a salon booking assistant. Help choose a service, preferred stylist and time slot, confirm the booking with a "
            "summary, and offer a reminder call a day before. Keep it cheerful and quick.",
            "Hello! Looking to book a salon appointment? Tell me which service you would like.",
            language="hi",
        ),
    ),
    Template(
        template_id="tmpl-property-inquiry",
        name="Property Inquiry Agent",
        industry="Real Estate",
        description="Picks up property calls instantly, answers listing questions, qualifies buyers or renters and schedules showings.",
        languages=["en"],
        agent_payload=_payload(
            "Property Inquiry Agent",
            "voice",
            "You are a real-estate assistant answering listing inquiries. Share price, size, location and availability, qualify budget "
            "and move-in timeline, and schedule a site visit at a convenient slot. Capture name and phone before hanging up.",
            "Hi! Thanks for your interest in our listing. Are you looking to buy or rent?",
        ),
    ),
]


def get_template(template_id: str) -> Template | None:
    for template in TEMPLATES:
        if template.template_id == template_id:
            return template
    return None
