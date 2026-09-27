"""Schema tests for the validated `extensions` namespace (spec 0043, Slice A).

Offline: every test builds ConversationConfig in memory. Pins the additive
default, each rejection class (syntax, count, per-value bytes, total bytes,
depth, serializability), byte-identical round-trips, and today's strictness
outside the namespace (unknown top-level keys still dropped).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from voiceai.modules.agents.constants import (
    EXTENSION_KEY_PATTERN,
    EXTENSION_MAX_DEPTH,
    MAX_EXTENSION_KEYS,
    MAX_EXTENSION_VALUE_BYTES,
    MAX_EXTENSIONS_TOTAL_BYTES,
)
from voiceai.modules.agents.models.agent import ConversationConfig


def _msgs(excinfo: pytest.ExceptionInfo[ValidationError]) -> str:
    """Combined validator messages (pydantic envelope input echo excluded)."""
    return " ".join(err["msg"] for err in excinfo.value.errors())


def test_constants_pin_spec_values():
    assert EXTENSION_KEY_PATTERN == r"^[A-Za-z][A-Za-z0-9_]{0,63}$"
    assert MAX_EXTENSION_KEYS == 32
    assert MAX_EXTENSION_VALUE_BYTES == 4096
    assert MAX_EXTENSIONS_TOTAL_BYTES == 32768
    assert EXTENSION_MAX_DEPTH == 3


def test_default_extensions_empty():
    assert ConversationConfig().extensions == {}


def test_omitted_extensions_validates():
    assert ConversationConfig.model_validate({}).extensions == {}


def test_valid_keys_accepted():
    cfg = ConversationConfig.model_validate(
        {"extensions": {"a": 1, "Flag_2": "x", "Z" * 64: True, "mixed_Case9": None}}
    )
    assert set(cfg.extensions) == {"a", "Flag_2", "Z" * 64, "mixed_Case9"}


def test_json_scalar_types_accepted():
    cfg = ConversationConfig.model_validate(
        {"extensions": {"s": "v", "i": 3, "f": 1.5, "b": True, "n": None, "l": [1, "a"], "d": {"k": 1}}}
    )
    assert cfg.extensions["n"] is None


@pytest.mark.parametrize(
    "key",
    ["__proto__", "dotted.key", "$evil", "1abc", "", "has space", "dash-key", "A" * 65],
)
def test_bad_syntax_rejected(key):
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {key: 1}})
    assert key in _msgs(excinfo)


@pytest.mark.parametrize("key", ["constructor", "prototype"])
def test_forbidden_names_rejected_despite_syntax(key):
    """Integrator close of A open Q1: the regex accepts constructor/prototype, so the denylist carries them."""
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {key: 1}})
    assert "forbidden extension key names" in _msgs(excinfo)
    assert key in _msgs(excinfo)


def test_over_count_rejected():
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {f"k{i}": i for i in range(MAX_EXTENSION_KEYS + 1)}})
    assert "too many extension keys" in _msgs(excinfo)


def test_count_boundary_accepted():
    cfg = ConversationConfig.model_validate({"extensions": {f"k{i}": i for i in range(MAX_EXTENSION_KEYS)}})
    assert len(cfg.extensions) == MAX_EXTENSION_KEYS


def test_over_bytes_per_value_rejected():
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {"big": "x" * 5000}})
    assert "byte cap" in _msgs(excinfo)
    assert "big" in _msgs(excinfo)


def test_per_value_byte_boundary_accepted():
    cfg = ConversationConfig.model_validate({"extensions": {"edge": "x" * 4094}})
    assert len(json.dumps(cfg.extensions["edge"]).encode("utf-8")) == MAX_EXTENSION_VALUE_BYTES


def test_total_bytes_rejected():
    ext = {f"t{i}": "y" * 3500 for i in range(10)}
    for val in ext.values():
        assert len(json.dumps(val).encode("utf-8")) < MAX_EXTENSION_VALUE_BYTES
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": ext})
    assert "total byte cap" in _msgs(excinfo)


def test_too_deep_rejected():
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {"deep": {"l1": {"l2": {"l3": {"l4": 1}}}}}})
    assert "nesting depth" in _msgs(excinfo)
    assert "deep" in _msgs(excinfo)


def test_depth_boundary_accepted():
    cfg = ConversationConfig.model_validate({"extensions": {"ok": {"l1": {"l2": 1}}}})
    assert cfg.extensions["ok"] == {"l1": {"l2": 1}}


@pytest.mark.parametrize("bad", [object(), {1, 2}, b"bytes"])
def test_non_json_serializable_rejected(bad):
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {"weird": bad}})
    assert "not JSON-serializable" in _msgs(excinfo)
    assert "weird" in _msgs(excinfo)


def test_violation_messages_name_keys_never_values():
    secret = "s3cr3t-value-must-not-echo"
    with pytest.raises(ValidationError) as excinfo:
        ConversationConfig.model_validate({"extensions": {"$bad.key!": secret}})
    combined = _msgs(excinfo)
    assert "$bad.key!" in combined
    assert secret not in combined


def test_round_trip_byte_identical():
    ext = {"flag": True, "threshold": 0.75, "label": "vip", "nested": {"a": [1, 2]}, "nothing": None}
    first = ConversationConfig.model_validate({"extensions": ext})
    dumped = first.model_dump()
    second = ConversationConfig.model_validate(dumped)
    assert second.model_dump() == dumped
    assert json.dumps(first.model_dump()["extensions"], sort_keys=True) == json.dumps(
        second.model_dump()["extensions"], sort_keys=True
    )


def test_unknown_top_level_keys_still_dropped():
    cfg = ConversationConfig.model_validate({"unknown_key": 1, "extensions": {"ok": 1}})
    assert "unknown_key" not in cfg.model_dump()
    assert cfg.extensions == {"ok": 1}
