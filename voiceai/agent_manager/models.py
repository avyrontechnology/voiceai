# legacy-shim(spec-0004) — ComponentLatencies lives in voiceai.modules.voice.models (step B3).
"""Pure re-export keeping ``from .models import ComponentLatencies`` (task_manager.py)
and every string patch against this path resolving; deleted at cutover, never grown."""

from voiceai.modules.voice.models import ComponentLatencies

__all__ = ["ComponentLatencies"]
