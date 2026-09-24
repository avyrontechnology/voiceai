"""Agent audit script invariants: report-only findings keyed by agent (spec 0022, slice 2)."""

from typing import Any

from voiceai.tooling.validate_agents import audit_many


def _entries() -> list[dict]:
    """Minimal catalog rows as plain mappings."""
    return [
        {"modality": "asr", "provider": "deepgram", "model": "nova-3", "models_open": True},
        {"modality": "llm", "provider": "openai", "model": "gpt-4o", "models_open": False},
    ]


def test_valid_documents_are_absent_from_the_report() -> None:
    """Clean rows produce no findings."""
    documents = [
        {
            "agent_id": "a-1",
            "config": {
                "tasks": [
                    {"tools_config": {"transcriber": {"provider": "deepgram", "model": "nova-3", "language": "en"}}}
                ]
            },
        }
    ]

    assert audit_many(documents, _entries()) == {}


def test_invalid_documents_report_problems_by_agent() -> None:
    """Typo'd rows report with paths and valid values; unparseable rows skip."""
    documents: list[dict[str, Any]] = [
        {
            "agent_id": "bad-1",
            "config": {
                "tasks": [
                    {
                        "tools_config": {
                            "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "english"},
                            "llm_agent": {"provider": "openai", "model": "gpt-9"},
                        }
                    }
                ]
            },
        },
        {"agent_id": "no-config"},
    ]

    report = audit_many(documents, _entries())

    assert set(report) == {"bad-1"}
    assert any("gpt-9" in problem for problem in report["bad-1"])
    assert any("english" in problem for problem in report["bad-1"])
