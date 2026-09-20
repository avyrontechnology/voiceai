"""DTMF flow: the keypad-digit queue consumer (spec 0004, B8).

The consumer body moved here VERBATIM from ``voiceai/agent_manager/task_manager.py``
(original tm 2982-3004): ``inject_digits_to_conversation``, the loop that drains the
``dtmf`` queue and injects each digit burst into the transcriber/LLM pipeline as a
``dtmf_number:``-prefixed text turn. The B5/B6/B7 seams apply unchanged:

* **Narrow facade, injected per call.** The function takes the live call session as
  its first parameter (kept named ``self`` so the body stays byte-identical). The
  session object is the legacy ``TaskManager`` instance, which INJECTS itself on
  every delegation — §3.1 bridge 3 — so this module imports no legacy engine code.
  `DtmfSession` is the typed facade of exactly what the consumer touches.
* **Same-named delegator stays on TaskManager.** ``inject_digits_to_conversation``
  keeps a thin same-named delegator on the class, so ``__init__``'s
  ``asyncio.create_task(self.inject_digits_to_conversation())`` scheduling,
  instance-attr mocks and ``__new__`` harnesses keep resolving.

**The tm:697 single-consumer guard stays at its ``__init__`` call site** (a
behavior-invariant checklist entry): the legacy constructor starts this consumer only
when ``dtmf_enabled and not self.__is_s2s()`` — an s2s task starts its own consumer in
``_run_s2s_conversation``, and starting this one too would race it for the same queue
and win by being first, injecting digits into a transcriber/LLM pipeline an s2s agent
does not have. The guard is constructor wiring, not consumer behavior, so it moves
with composition (B13a), and B1's construction matrix pins it until then.

One compile-time name-mangling accommodation inside the otherwise-verbatim body (the
B5/B6/B7 precedent): ``self.__get_updated_meta_info`` is spelled
``self._TaskManager__get_updated_meta_info``, which is exactly what the class body
always compiled to. The signature gained type annotations (rule 6), the function a
docstring (rule 7), and the module logs through ``otobaai`` (rule 3; log content
preserved, including the preserved quirk that the failure log line reuses the
success wording at INFO — owned by ``revamp/resilient-core``, R8, never re-fixed
here).
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from voiceai.common.logger import get_logger
from voiceai.modules.voice.constants import MODULE_NAME

logger = get_logger(MODULE_NAME)

__all__ = [
    "DtmfSession",
    "inject_digits_to_conversation",
]


class DtmfSession(Protocol):
    """The narrow facade of the live call session the DTMF consumer drives.

    Structurally satisfied by the legacy ``TaskManager`` (which passes itself in; it
    is never imported here). Attribute groups mirror the legacy instance state the
    moved body reads and writes; the ``_TaskManager__*`` member is the session's own
    private method reached back through its mangled name, so a
    ``patch.object(TaskManager, ...)`` or instance-attr mock intercepts internal
    dispatch too.
    """

    # --- the queue topology and the per-call DTMF ledger ---
    queues: dict
    dtmf_events: list
    conversation_start_init_ts: float

    # --- collaborators ---
    tools: dict

    # --- legacy session methods the consumer calls back into ---
    def _TaskManager__get_updated_meta_info(self, meta_info: Any = ...) -> Any: ...  # noqa: D102
    async def _handle_transcriber_output(self, next_task: Any, transcriber_message: Any, meta_info: Any) -> Any: ...  # noqa: D102


async def inject_digits_to_conversation(self: DtmfSession) -> None:
    """Drain the dtmf queue forever, injecting each digit burst as an LLM text turn.

    Each burst is stamped into the session's ``dtmf_events`` ledger with its offset
    from conversation start, then routed through ``_handle_transcriber_output`` as a
    ``dtmf_number:``-prefixed message with fresh turn metadata. Per-iteration
    exceptions are isolated so one bad burst never kills the consumer;
    ``asyncio.CancelledError`` propagates for teardown.
    """
    while True:
        try:
            dtmf_digits = await self.queues["dtmf"].get()
            logger.info(f"DTMF collected {dtmf_digits}")

            _dtmf_ts = round(time.time() * 1000 - self.conversation_start_init_ts, 2)
            for _digit in dtmf_digits:
                self.dtmf_events.append({"digit": _digit, "ts_ms": _dtmf_ts})

            dtmf_message = "dtmf_number: " + dtmf_digits
            base_meta_info = {
                "io": self.tools["input"].io_provider,
                "type": "text",
                "sequence": 0,
                "origin": "dtmf",
            }
            meta_info = self._TaskManager__get_updated_meta_info(base_meta_info)
            await self._handle_transcriber_output("llm", dtmf_message, meta_info)
            logger.info(f"DTMF LLM processing triggered with sequence_id={meta_info['sequence_id']}")
        except Exception as e:
            logger.info(f"DTMF LLM processing triggered with exception {e}")
