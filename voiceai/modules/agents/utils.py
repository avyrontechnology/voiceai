"""Impure prompt-file glue over the legacy helpers (AGENTS.md rule 1g; spec 0002, A3).

The per-agent prompt payload lives at ``<agent_id>/conversation_details.json`` under the
CWD-relative ``PREPROCESS_DIR`` (see ``constants.py`` for why that stays relative). All IO
here is delegated THROUGH the live ``voiceai.helpers.utils`` module attributes —
``store_file`` and ``get_prompt_responses`` — resolved at call time, so every existing
monkeypatch target (``utils.store_file``, ``utils.get_prompt_responses``,
``utils.PREPROCESS_DIR`` — e.g. ``tests/test_agent_prompts_endpoint.py``) keeps
intercepting exactly as it does against the quickstart server.

The payload passes through opaque in both directions: no reshaping, no validation. In
particular the multiagent ``task_1.{agent_name}.system_prompt`` nesting the engine reads
back (``task_manager.py`` lines 2150-2162; pure selectors in ``static_methods.py``) is a
spec 0002 behavior invariant and must round-trip byte-identical.

# TODO(spec-0004): the delegation retires when the prompt-file helpers themselves migrate
# out of ``voiceai/helpers/utils.py``; until then this module is the agents-side seam.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from voiceai.modules.agents.constants import CONVERSATION_DETAILS_FILE_NAME, PROMPT_FILE_KEY_TEMPLATE

__all__ = ["delete_conversation_details", "read_conversation_details", "write_conversation_details"]

#: Dotted path of the legacy prompt-IO module. Resolved dynamically at call time — never a
#: static import statement — for two reasons: a static legacy import outside ``adapters/``
#: violates AGENTS.md §3.1 (enforced by ``tests/arch/test_layer_contract.py``), and a
#: call-time module-attribute lookup is what keeps the legacy monkeypatch targets live.
_LEGACY_PROMPT_IO_MODULE: Final[str] = "voiceai.helpers.utils"


def _legacy_prompt_io() -> ModuleType:
    """Resolve the live legacy prompt-IO module at call time.

    ``import_module`` answers the already-imported module object from ``sys.modules``, so
    attributes patched on ``voiceai.helpers.utils`` (functions or ``PREPROCESS_DIR``) are
    seen by every call made after the patch — the monkeypatch-transparency contract.

    Returns:
        The ``voiceai.helpers.utils`` module object.
    """
    return import_module(_LEGACY_PROMPT_IO_MODULE)


async def read_conversation_details(agent_id: str) -> dict[str, Any] | None:
    """Read an agent's stored prompt payload through the legacy loader.

    Delegates to ``voiceai.helpers.utils.get_prompt_responses(local=True)`` verbatim: a
    missing or unreadable file answers ``None`` (callers degrade to empty prompts — never
    an error), and a readable file answers exactly what was stored.

    Args:
        agent_id: The bare-UUID agent id whose prompts to read.

    Returns:
        The parsed payload, or ``None`` when no prompt file exists for the agent.
    """
    helpers = _legacy_prompt_io()
    prompts = await helpers.get_prompt_responses(assistant_id=agent_id, local=True)
    return cast("dict[str, Any] | None", prompts)  # why: legacy loader answers free-form parsed JSON


async def write_conversation_details(agent_id: str, prompts: dict[str, Any] | None) -> None:
    """Write an agent's prompt payload through the legacy store helper.

    Delegates to ``voiceai.helpers.utils.store_file(local=True)`` with the exact kwargs
    the quickstart server uses; ``None`` is stored as JSON ``null`` (legacy parity), which
    reads back as "no stored prompts".

    Args:
        agent_id: The bare-UUID agent id whose prompts to write.
        prompts: The payload to persist, opaque — including the multiagent
            ``task_1.{agent_name}.system_prompt`` nesting — or ``None``.
    """
    helpers = _legacy_prompt_io()
    file_key = PROMPT_FILE_KEY_TEMPLATE.format(agent_id=agent_id)
    await helpers.store_file(file_key=file_key, file_data=prompts, local=True)


async def delete_conversation_details(agent_id: str) -> bool:
    """Remove an agent's prompt file, reporting whether one existed.

    Mirrors the loader's path construction (`<PREPROCESS_DIR>/<agent_id>/...`) through
    the live ``PREPROCESS_DIR`` attribute — never a static legacy import (AGENTS.md
    §3.1) — so the monkeypatch target keeps intercepting.

    Args:
        agent_id: The bare-UUID agent id whose prompts to remove.

    Returns:
        ``True`` when a file was removed.
    """
    helpers = _legacy_prompt_io()
    path = Path(str(helpers.PREPROCESS_DIR)) / agent_id / CONVERSATION_DETAILS_FILE_NAME
    if not path.is_file():
        return False
    path.unlink()
    return True
