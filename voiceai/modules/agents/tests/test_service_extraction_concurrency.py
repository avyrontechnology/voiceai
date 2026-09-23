"""Extraction fan-out: order, bound, and guard parity under concurrency (spec 0012).

Offline: an instrumented `LlmPort` fake records in-flight overlap; the service must
generate N extraction prompts concurrently (not serially), assign results back by
task index, never exceed `MAX_EXTRACTION_CONCURRENCY`, and keep the UPDATE-only
guard firing once per extraction task in walk order.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from voiceai.modules.agents.constants import MAX_EXTRACTION_CONCURRENCY
from voiceai.modules.agents.models import AgentModel
from voiceai.modules.agents.service import AgentService

EXTRACTION_SYSTEM_PROMPT = "You are a parsing assistant."


class FakeDefinitionStore:
    """Minimal `AgentDefinitionPort` fake for seeding flows."""

    def __init__(self, records=None) -> None:
        self.records = dict(records or {})

    async def get_agent(self, agent_id):
        """Serve stored configs."""
        return self.records.get(agent_id)

    async def save_agent(self, agent_id, config):
        """Store."""
        self.records[agent_id] = config

    async def delete_agent(self, agent_id):
        """Drop; report prior existence."""
        return self.records.pop(agent_id, None) is not None

    async def list_agents(self):
        """Empty directory."""
        return []


class FakePromptStore:
    """Minimal `AgentSessionStorePort` fake."""

    def __init__(self) -> None:
        self.stored: dict = {}

    async def get_prompts(self, agent_id):
        """Answer `None` (no prompts seeded)."""
        return self.stored.get(agent_id)

    async def save_prompts(self, agent_id, prompts):
        """Store."""
        self.stored[agent_id] = prompts

    async def delete_prompts(self, agent_id):
        """Report prior existence."""
        existed = agent_id in self.stored
        self.stored.pop(agent_id, None)
        return existed


class TrackingLlm:
    """`LlmPort` fake with in-flight overlap tracking and per-detail answers."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.details_in_start_order: list[str] = []

    async def __call__(self, messages):
        """Overlap, sleep past one loop tick, answer from the user turn."""
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            details = messages[1]["content"]
            self.details_in_start_order.append(details)
            await asyncio.sleep(0.02)
            return '{"extracted": "' + str(details) + '"}'
        finally:
            self.in_flight -= 1


class GuardSpy:
    """Counts UPDATE-only guard invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        """Count."""
        self.calls += 1


def extraction_task(details: str) -> dict[str, Any]:
    """One extraction task carrying distinct details."""
    return {
        "task_type": "extraction",
        "tools_config": {"llm_agent": {"extraction_details": details}},
        "toolchain": {"execution": "sequential", "pipelines": []},
    }


def build_service(
    llm: TrackingLlm, guard: GuardSpy | None = None
) -> tuple[AgentService, FakeDefinitionStore]:
    """Assemble a service around fakes; return the service with its store."""
    store = FakeDefinitionStore()
    service = AgentService(
        definitions=store,
        prompt_store=FakePromptStore(),
        extraction_llm=llm,
        require_extraction_model=guard if guard is not None else GuardSpy(),
        extraction_system_prompt=EXTRACTION_SYSTEM_PROMPT,
        logger=logging.getLogger("otobaai.test.agents.fanout"),
    )
    return service, store


def agent_model(*tasks: dict[str, Any]) -> AgentModel:
    """Validate a minimal definition around the given tasks."""
    return AgentModel.model_validate({"agent_name": "Support", "tasks": list(tasks)})


async def test_create_generates_three_extractions_concurrently_in_task_order() -> None:
    """Three tasks overlap (max in-flight 3) and land back on their own indexes."""
    llm = TrackingLlm()
    service, store = build_service(llm)
    result = await service.create_agent(agent_model(extraction_task("d1"), extraction_task("d2"), extraction_task("d3")), None)

    stored_tasks = store.records[result["agent_id"]]["tasks"]
    assert [task["tools_config"]["llm_agent"]["extraction_json"] for task in stored_tasks] == [
        '{"extracted": "d1"}',
        '{"extracted": "d2"}',
        '{"extracted": "d3"}',
    ]
    assert llm.max_in_flight == 3  # serial code would pin this to 1


async def test_fan_out_never_exceeds_the_bound() -> None:
    """Eight tasks overlap but never breach `MAX_EXTRACTION_CONCURRENCY`."""
    llm = TrackingLlm()
    service, _ = build_service(llm)
    await service.create_agent(agent_model(*[extraction_task(f"d{i}") for i in range(8)]), None)
    assert llm.max_in_flight <= MAX_EXTRACTION_CONCURRENCY
    assert llm.max_in_flight > 1


async def test_update_keeps_the_per_task_guard_and_task_order() -> None:
    """The UPDATE-only guard still fires once per extraction task, in walk order."""
    llm = TrackingLlm()
    guard = GuardSpy()
    service, store = build_service(llm, guard)
    store.records["a-1"] = {"agent_name": "Old", "tasks": []}
    result = await service.update_agent(
        "a-1", agent_model(extraction_task("u1"), extraction_task("u2")), None
    )
    assert result == {"agent_id": "a-1", "state": "updated"}
    assert guard.calls == 2
    stored_tasks = store.records["a-1"]["tasks"]
    assert [task["tools_config"]["llm_agent"]["extraction_json"] for task in stored_tasks] == [
        '{"extracted": "u1"}',
        '{"extracted": "u2"}',
    ]
