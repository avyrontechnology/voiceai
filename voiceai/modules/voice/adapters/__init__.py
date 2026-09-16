"""Strangler-bridge adapters: the only voice files sanctioned to import legacy code (AGENTS.md §3.1).

Two kinds of bridge live under this package (spec 0004, step B3):

* The per-seam factory modules — ``transcription`` / ``synthesis`` / ``telephony`` /
  ``llm`` / ``s2s`` — hold the legacy provider-class imports and typed factories over
  the ``SUPPORTED_*`` registries (`voiceai.modules.voice.registry` builds the maps from
  the classes these modules import).
* This package surface re-exports the few light legacy VALUES that non-adapter voice
  files consume (the spec-0002 A4 ``EXTRACTION_SYSTEM_PROMPT`` precedent), so
  `voiceai.modules.voice.static_methods` (rule 1g) never imports legacy itself.

Importing this package stays deliberately light: the factory submodules — which drag the
full provider stacks in — load only when imported explicitly, never from here (an arch
canary pins that `static_methods` pulls in no provider package).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

# §3.1 bridge imports — retire when the constants library migrates out of the legacy tree
# (the tool definitions move with the turn steps' endgame; spec 0004 non-goal for now).
from voiceai.constants import END_CALL_FUNCTION_PREFIX as _LEGACY_END_CALL_FUNCTION_PREFIX
from voiceai.constants import END_CALL_TOOL_DEFINITION as _LEGACY_END_CALL_TOOL_DEFINITION

# §3.1 bridge import — retires with the helpers audio-DSP physical move (an explicit
# spec 0004 non-goal; the endgame spec owns it).
from voiceai.helpers.utils import resample as _legacy_resample

__all__ = ["END_CALL_FUNCTION_PREFIX", "END_CALL_TOOL_DEFINITION", "resample"]

#: The internal end-call tool's name/key, identical to the legacy constant by identity
#: (an arch test pins it): `static_methods._inject_end_call_tool` keys ``tools_params``
#: with it and the function-call steps match tool names against it.
END_CALL_FUNCTION_PREFIX: Final[str] = _LEGACY_END_CALL_FUNCTION_PREFIX

#: The internal end-call tool definition (OpenAI function-tool JSON shape), identical to
#: the legacy dict by identity — consumers deepcopy before mutating, exactly as the
#: legacy call sites always did.
END_CALL_TOOL_DEFINITION: Final[dict[str, Any]] = _LEGACY_END_CALL_TOOL_DEFINITION  # why: legacy tool-def JSON shape

#: The legacy audio resampler, typed at this boundary: every branch answers raw audio
#: bytes (the pcm path `static_methods.welcome_pcm_upsampled` uses returns PCM-16).
resample: Final[Callable[..., bytes]] = _legacy_resample  # why: the legacy DSP helper takes free-form kwargs
