"""Settings truth pins (spec 0042, Slice D; integrator-closed): every call-behavior key proven or removed.

Mechanical drift gate over the Slice A schema outcome. ``FIELD_CONSUMERS`` maps every
live ``ConversationConfig`` field to the runtime files that read it; the gate fails on
drift in either direction (a field without a consumer, a consumer entry for a deleted
field). ``ambient_noise`` is the one key Slice B could not wire: allowlisted in
``REMOVED_FIELDS`` and required absent from schema and both API docs. Slice B wired
``interruption_backoff_period`` after Slice A's delete-default, so the integrator
restored it with consumer pins; the Slice A additions (``recording`` plus the
promoted hidden keys) must remain present in the schema and both docs. Consumer
checks are textual (``field in file text``) and import nothing, following the
``test_tenant_isolation.py`` pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

from voiceai.modules.agents.models import ConversationConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = REPO_ROOT / "openapi.yaml"
API_REFERENCE_PATH = REPO_ROOT / "API_REFERENCE.md"
SOURCE_ENCODING = "utf-8"
SCHEMA_BLOCK_MARKER = "    ConversationConfig:\n"
SCHEMA_BLOCK_END = re.compile(r"\n    \S")
DOCS_SECTION_MARKER = "### ConversationConfig\n"

#: Runtime files that read ConversationConfig keys (verified by reading; file:line in comments).
CONFIG_PARSE = "voiceai/modules/voice/session/config.py"
COMPOSE = "voiceai/modules/voice/session/composition.py"
INTERRUPT = "voiceai/modules/voice/session/interruption.py"
LISTENER = "voiceai/modules/voice/session/turn/transcript_listener.py"
HANGUP = "voiceai/modules/voice/session/lifecycle/hangup.py"
PROMPTS = "voiceai/modules/voice/session/prompts.py"
FUNCCALLS = "voiceai/modules/voice/session/turn/function_calls.py"
S2S_RUNNER = "voiceai/modules/voice/session/s2s_runner.py"
VOICEMAIL_HANDLER = "voiceai/agent_manager/voicemail_handler.py"
WELCOME = "voiceai/modules/voice/session/welcome.py"
HEALTH = "voiceai/modules/voice/session/health.py"
REPORT = "voiceai/modules/voice/session/lifecycle/report.py"
SWITCHER = "voiceai/modules/voice/session/language/switcher.py"

#: Every live ConversationConfig field maps to the runtime files that read it.
FIELD_CONSUMERS: dict[str, tuple[str, ...]] = {
    # config.py:422 folds the flag into process_interim_results for the transcriber leg.
    "optimize_latency": (CONFIG_PARSE,),
    # config.py:425 parses hang_conversation_after; composition.py:519 assigns it,
    # hangup.py:595 enforces the inactivity hangup (derived attr, not the literal).
    "hangup_after_silence": (CONFIG_PARSE,),
    # config.py:424; composition.py:516,576; interruption.py:48,66,129 audio gate; listener.py:230,908 grace check.
    "incremental_delay": (CONFIG_PARSE, COMPOSE, INTERRUPT, LISTENER),
    # config.py:433; composition.py:567,574; interruption.py:46,71,172 barge-in gate; listener.py:231,915,931.
    "number_of_words_for_interruption": (CONFIG_PARSE, COMPOSE, INTERRUPT, LISTENER),
    # config.py:382 parses use_llm_to_determine_hangup; completion + end_call flow hangs off the derived flag.
    "hangup_after_LLMCall": (CONFIG_PARSE,),
    # config.py:196 completion prompt fallback and config.py:224 end_call description.
    "call_cancellation_prompt": (CONFIG_PARSE,),
    # config.py:435 folds the flag into should_backchannel; composition.py:581,587 starts the loop off it.
    "backchanneling": (CONFIG_PARSE, COMPOSE),
    # config.py:437; composition.py:584-585; hangup.py:701 backchannel loop sleep.
    "backchanneling_message_gap": (CONFIG_PARSE, COMPOSE, HANGUP),
    # config.py:436; composition.py:583; hangup.py:682,699 user-speech-duration gate.
    "backchanneling_start_delay": (CONFIG_PARSE, COMPOSE, HANGUP),
    # hangup.py:542-564 Slice B telephony arm + web arm share _max_call_duration_s (spec 0042).
    "call_terminate": (HANGUP,),
    # config.py:426; composition.py:522; prompts.py:107,280,307 filler-note injection.
    "use_fillers": (CONFIG_PARSE, COMPOSE, PROMPTS),
    # config.py:417-418; composition.py:503; hangup.py:604,606 silence-nudge trigger.
    "trigger_user_online_message_after": (CONFIG_PARSE, COMPOSE, HANGUP),
    # config.py:372,421; composition.py:506; hangup.py:615 nudge text (output_loop.py:584 is commented out: excluded).
    "check_user_online_message": (CONFIG_PARSE, COMPOSE, HANGUP),
    # config.py:420; composition.py:504; hangup.py:614 gate; funccalls.py:178,397,530 re-arm after tool calls.
    "check_if_user_online": (CONFIG_PARSE, COMPOSE, HANGUP, FUNCCALLS),
    # config.py:416; composition.py:495,499 telephony consumer guard; s2s_runner.py:316 s2s consumer start.
    "dtmf_enabled": (CONFIG_PARSE, COMPOSE, S2S_RUNNER),
    # voicemail_handler.py:22 enabled flag, fed the whole conversation_config by composition.py:560.
    "voicemail": (VOICEMAIL_HANDLER, COMPOSE),
    # voicemail_handler.py:32 detection window (legacy reader behind the composition seam).
    "voicemail_detection_duration": (VOICEMAIL_HANDLER,),
    # voicemail_handler.py:30 interim check throttle (legacy reader behind the composition seam).
    "voicemail_check_interval": (VOICEMAIL_HANDLER,),
    # voicemail_handler.py:31 interim transcript word floor (legacy reader behind the composition seam).
    "voicemail_min_transcript_length": (VOICEMAIL_HANDLER,),
    # Slice B wired the explicit flag read (integrator-closed): config.py:344,403-404,475
    # parse with None-absent sentinel; composition.py:336-341 explicit-wins override;
    # report.py carries the capture outcome (Slice C).
    "recording": (COMPOSE, REPORT),
    # Slice B wiring (integrator-restored after the delete-default): config.py:343,474
    # parse; composition.py:589 assign; interruption.py:141,265 barge-in hold gate.
    "interruption_backoff_period": (CONFIG_PARSE, COMPOSE, INTERRUPT),
    # Slice A promotion of the hidden key: config.py:308,378-380,429 parse; composition.py:528 assign;
    # welcome.py:120,399-407 context substitution; s2s_runner.py:93,354,772 + hangup.py:125,326,471 playout;
    # funccalls.py:140,238 seam facade.
    "call_hangup_message": (CONFIG_PARSE, COMPOSE, WELCOME, S2S_RUNNER, HANGUP, FUNCCALLS),
    # Slice A promotion of the hidden key: config.py:287,405 parse; composition.py:283 assign;
    # welcome.py:105,146 pre-welcome sleep (milliseconds, pinned); health.py:147-148 latency baseline.
    "welcome_message_delay": (CONFIG_PARSE, COMPOSE, WELCOME, HEALTH),
    # Integrator promotion of the proven-live hidden key: config.py:471 parse;
    # composition.py:617 assign; transcript_listener.py:833,856 pre-welcome speech drop.
    "discard_pre_welcome_utterance": (CONFIG_PARSE, COMPOSE, LISTENER),
    # Integrator promotion of the proven-live hidden keys: config.py:440-441 parse;
    # composition.py:398-399 assign; switcher.py:1203-1216 system_only/per_turn injection.
    "language_injection_mode": (CONFIG_PARSE, COMPOSE, SWITCHER),
    "language_instruction_template": (CONFIG_PARSE, COMPOSE, SWITCHER),
    # Integrator promotion of the proven-live hidden key: config.py:265-269 derives
    # end_call_primary; enforcement rides the hangup goal-check path off the derived flag.
    "end_call_tool_mode": (CONFIG_PARSE,),
}

#: The one key Slice B could not wire: deleted by Slice A, must stay out of schema + docs.
REMOVED_FIELDS: frozenset[str] = frozenset({"ambient_noise"})


def _consumer_failures() -> list[str]:
    """Collect consumer entries whose file is missing or never names its field.

    Returns:
        One line per broken ``(field, file)`` pair; empty when the map is exact.
    """
    failures: list[str] = []
    for field, rel_paths in FIELD_CONSUMERS.items():
        for rel_path in rel_paths:
            path = REPO_ROOT / rel_path
            if not path.is_file():
                failures.append(f"{field}: missing consumer file {rel_path}")
            elif field not in path.read_text(encoding=SOURCE_ENCODING):
                failures.append(f"{field}: {rel_path} no longer references the key")
    return failures


def _openapi_conversation_block() -> str:
    """Return the raw ``ConversationConfig`` schema block from ``openapi.yaml``."""
    text = OPENAPI_PATH.read_text(encoding=SOURCE_ENCODING)
    start = text.index(SCHEMA_BLOCK_MARKER)
    tail = text[start + len(SCHEMA_BLOCK_MARKER):]
    match = SCHEMA_BLOCK_END.search(tail)
    assert match is not None, "ConversationConfig block has no terminating schema entry"
    return tail[: match.start()]


def _api_reference_conversation_section() -> str:
    """Return the ``ConversationConfig`` table from ``API_REFERENCE.md``."""
    text = API_REFERENCE_PATH.read_text(encoding=SOURCE_ENCODING)
    start = text.index(DOCS_SECTION_MARKER)
    tail = text[start + len(DOCS_SECTION_MARKER):]
    end = tail.index("\n### ")
    return tail[:end]


def test_schema_fields_match_consumer_map_exactly() -> None:
    """Every schema field has a consumer entry and no entry points at a deleted field."""
    schema_fields = set(ConversationConfig.model_fields)
    mapped_fields = set(FIELD_CONSUMERS)
    assert schema_fields == mapped_fields, (
        "settings truth drift (spec 0042: wire the key or delete it in the same slice):\n"
        + "\n".join(f"+ {field} (in schema, missing from FIELD_CONSUMERS)" for field in sorted(schema_fields - mapped_fields))
        + "\n".join(f"- {field} (in FIELD_CONSUMERS, gone from schema)" for field in sorted(mapped_fields - schema_fields))
    )


def test_each_consumer_file_references_its_field() -> None:
    """Each allowlisted consumer exists and textually references its field (grep, no imports)."""
    assert _consumer_failures() == []


def test_removed_fields_absent_from_schema() -> None:
    """Proven-dead keys stay out of the validated write path (prove-or-remove)."""
    survivors = REMOVED_FIELDS & set(ConversationConfig.model_fields)
    assert not survivors, f"dead keys resurrected on ConversationConfig: {sorted(survivors)}"


def test_removed_fields_absent_from_docs() -> None:
    """Proven-dead keys stay out of the API contract documents."""
    openapi_text = OPENAPI_PATH.read_text(encoding=SOURCE_ENCODING)
    reference_text = API_REFERENCE_PATH.read_text(encoding=SOURCE_ENCODING)
    leaked = [
        f"{field} in {label}"
        for field in sorted(REMOVED_FIELDS)
        for label, body in (("openapi.yaml", openapi_text), ("API_REFERENCE.md", reference_text))
        if field in body
    ]
    assert leaked == []


def test_schema_fields_present_in_openapi() -> None:
    """Every schema field is documented in the openapi ``ConversationConfig`` block."""
    block = _openapi_conversation_block()
    missing = [field for field in ConversationConfig.model_fields if f"{field}:" not in block]
    assert missing == []


def test_schema_fields_present_in_api_reference() -> None:
    """Every schema field is documented in the API reference ``ConversationConfig`` table."""
    section = _api_reference_conversation_section()
    missing = [field for field in ConversationConfig.model_fields if f"`{field}`" not in section]
    assert missing == []
