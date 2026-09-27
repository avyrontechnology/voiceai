"""Pure merge + audit tests for the validated `extensions` namespace (spec 0043, Slice B).

Offline, no I/O: the `apply_agent_patch` merge matrix (add/overwrite/no-op-null/
nested), per-task `clear_extensions` (hit, miss -> problem, empty -> no-op, applied
after the merge), and the `audit_provider_config` exemption pins (a garbage provider
name inside `extensions` produces no problem; the same name outside still does).
"""

from __future__ import annotations

from typing import Any

from voiceai.modules.agents.static_methods import apply_agent_patch, audit_provider_config


def _stored(*, extensions: dict[str, Any] | None = None) -> dict[str, Any]:
    """One stored dump whose first task carries the given extensions bag (or none)."""
    task: dict[str, Any] = {"task_type": "conversation", "tools_config": {}}
    if extensions is not None:
        task["task_config"] = {"extensions": dict(extensions)}
    return {"agent_name": "Support", "tasks": [task]}


def _patch_task(operation: dict[str, Any]) -> dict[str, Any]:
    """Wrap one per-task op, addressed at task 0, in a patch body."""
    return {"tasks_patch": [dict({"task_index": 0}, **operation)]}


def _extensions_of(merged: dict[str, Any]) -> dict[str, Any]:
    """Read back the merged extensions bag."""
    extensions: dict[str, Any] = merged["tasks"][0]["task_config"]["extensions"]
    return extensions


def _rows() -> list[dict[str, Any]]:
    """Minimal dumped catalog rows for the pure audit tests."""
    return [
        {"modality": "llm", "provider": "openai", "model": "gpt-4o"},
        {"modality": "asr", "provider": "deepgram", "model": "nova-3", "models_open": True},
    ]


def _is_valid_language(code: str) -> bool:
    """Tiny BCP-47 stand-in (only the codes these tests use)."""
    return code in ("en", "hi")


def test_extensions_added_to_task_without_bag() -> None:
    """A patch creates the bag when the stored task has no `task_config`."""
    merged, _, _, problems = apply_agent_patch(_stored(), _patch_task({"task_config": {"extensions": {"flag": True}}}))

    assert problems == []
    assert _extensions_of(merged) == {"flag": True}


def test_extensions_merge_adds_and_overwrites() -> None:
    """Present keys win; absent stored keys survive; other tasks untouched."""
    stored = _stored(extensions={"keep": 1, "overwrite": "old"})
    merged, _, _, problems = apply_agent_patch(
        stored, _patch_task({"task_config": {"extensions": {"overwrite": "new", "added": [1, 2]}}})
    )

    assert problems == []
    assert _extensions_of(merged) == {"keep": 1, "overwrite": "new", "added": [1, 2]}


def test_extensions_present_none_is_noop() -> None:
    """Null values (and a null bag) change nothing — deletion needs the clear list."""
    stored = _stored(extensions={"keep": 1})
    merged, _, _, problems = apply_agent_patch(
        stored, _patch_task({"task_config": {"extensions": {"keep": None, "fresh": None}}})
    )

    assert problems == []
    assert _extensions_of(merged) == {"keep": 1}

    merged, _, _, problems = apply_agent_patch(stored, _patch_task({"task_config": {"extensions": None}}))

    assert problems == []
    assert _extensions_of(merged) == {"keep": 1}


def test_extensions_nested_merge_recurses() -> None:
    """Nested dicts merge key-by-key through the existing recursion, nulls included."""
    stored = _stored(extensions={"nested": {"keep": 1, "drop": 2}, "top": "old"})
    merged, _, _, problems = apply_agent_patch(
        stored,
        _patch_task({"task_config": {"extensions": {"nested": {"drop": None, "added": 3}, "top": "new"}}}),
    )

    assert problems == []
    assert _extensions_of(merged) == {"nested": {"keep": 1, "drop": 2, "added": 3}, "top": "new"}


def test_extensions_merge_leaves_other_task_config_keys_alone() -> None:
    """The merge never crosses the namespace boundary into first-class fields."""
    merged, _, _, problems = apply_agent_patch(
        _stored(extensions={"flag": True}),
        _patch_task({"task_config": {"extensions": {"other": 2}, "hangup_after_silence": 5}}),
    )

    assert problems == []
    assert _extensions_of(merged) == {"flag": True, "other": 2}
    assert merged["tasks"][0]["task_config"]["hangup_after_silence"] == 5


def test_merge_and_clear_never_mutate_stored() -> None:
    """The stored dump keeps its bag intact after a merge plus a clear."""
    stored = _stored(extensions={"gone": 1, "stays": 2})
    apply_agent_patch(
        stored,
        _patch_task({"task_config": {"extensions": {"fresh": 3}}, "clear_extensions": ["gone"]}),
    )

    assert stored["tasks"][0]["task_config"]["extensions"] == {"gone": 1, "stays": 2}


def test_clear_extensions_hit_drops_only_named_keys() -> None:
    """Named keys drop; the rest of the bag survives."""
    merged, _, _, problems = apply_agent_patch(
        _stored(extensions={"gone": 1, "stays": 2}), _patch_task({"clear_extensions": ["gone"]})
    )

    assert problems == []
    assert _extensions_of(merged) == {"stays": 2}


def test_clear_extensions_miss_is_problem_and_applies_nothing() -> None:
    """Unknown names report (strict parity with `clear`) and drop nothing."""
    merged, _, _, problems = apply_agent_patch(
        _stored(extensions={"stays": 2}), _patch_task({"clear_extensions": ["ghost"]})
    )

    assert problems == ["tasks[0].clear_extensions: unknown target 'ghost'"]
    assert _extensions_of(merged) == {"stays": 2}


def test_clear_extensions_miss_without_bag_is_problem() -> None:
    """With no bag at all, every name is unknown."""
    _, _, _, problems = apply_agent_patch(_stored(), _patch_task({"clear_extensions": ["ghost"]}))

    assert problems == ["tasks[0].clear_extensions: unknown target 'ghost'"]


def test_clear_extensions_empty_and_null_are_noops() -> None:
    """An empty list or a present-null changes nothing and reports nothing."""
    operation: dict[str, Any]
    for operation in ({"clear_extensions": []}, {"clear_extensions": None}, {}):
        merged, _, _, problems = apply_agent_patch(_stored(extensions={"stays": 2}), _patch_task(operation))

        assert problems == []
        assert _extensions_of(merged) == {"stays": 2}


def test_clear_extensions_non_list_is_problem() -> None:
    """A non-list value reports and drops nothing."""
    merged, _, _, problems = apply_agent_patch(
        _stored(extensions={"gone": 1}), _patch_task({"clear_extensions": "gone"})
    )

    assert problems == ["tasks[0].clear_extensions must be a list"]
    assert _extensions_of(merged) == {"gone": 1}


def test_clear_extensions_applies_after_merge() -> None:
    """Keys added by the same op clear too — drops run after the merge."""
    merged, _, _, problems = apply_agent_patch(
        _stored(extensions={"old": 1}),
        _patch_task({"task_config": {"extensions": {"fresh": 3}}, "clear_extensions": ["fresh", "old"]}),
    )

    assert problems == []
    assert _extensions_of(merged) == {}


def test_audit_ignores_garbage_provider_inside_task_config_extensions() -> None:
    """Provider-shaped keys under `task_config.extensions` produce no problems."""
    config = {
        "tasks": [
            {
                "tools_config": {"llm_agent": {"provider": "openai", "model": "gpt-4o"}},
                "task_config": {"extensions": {"provider": "garbage-provider", "model": "nope", "anything": [1, 2]}},
            }
        ]
    }

    assert audit_provider_config(config, _rows(), _is_valid_language) == []


def test_audit_reports_same_garbage_provider_outside_extensions() -> None:
    """The identical name under `tools_config` still fails with the valid values."""
    config = {"tasks": [{"tools_config": {"llm_agent": {"provider": "garbage-provider", "model": "gpt-4o"}}}]}

    problems = audit_provider_config(config, _rows(), _is_valid_language)

    assert any("garbage-provider" in problem and "openai" in problem for problem in problems)


def test_audit_ignores_extensions_nested_inside_llm_subtree() -> None:
    """The exemption holds at any depth of the recursive walk, by key not value."""
    config = {
        "tasks": [
            {
                "tools_config": {
                    "llm_agent": {
                        "provider": "openai",
                        "model": "gpt-4o",
                        "nested": {"provider": "openai", "model": "gpt-4o"},
                        "extensions": {"provider": "inside-garbage", "model": "nope"},
                    }
                }
            }
        ]
    }

    assert audit_provider_config(config, _rows(), _is_valid_language) == []


def test_audit_still_reports_invalid_sibling_beside_extensions() -> None:
    """Skipping `extensions` never short-circuits the rest of the walk."""
    config = {
        "tasks": [
            {
                "tools_config": {
                    "llm_agent": {
                        "provider": "openai",
                        "model": "gpt-4o",
                        "extensions": {"provider": "inside-garbage", "model": "nope"},
                        "broken": {"provider": "outside-garbage", "model": "gpt-4o"},
                    }
                }
            }
        ]
    }

    problems = audit_provider_config(config, _rows(), _is_valid_language)

    assert any("outside-garbage" in problem for problem in problems)
    assert not any("inside-garbage" in problem for problem in problems)
