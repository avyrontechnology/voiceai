"""Legacy re-export shim for the engine execution hook (spec 0080).

The engine-side execution writer moved to `voiceai.modules.executions.runner`
with the rest of the call-record family. This file stays as an import path for
the realtime engine's existing `from voiceai.platform.engine_hook import ...`
sites and is deleted at the engine cutover.
"""

# legacy-shim(spec-0080)
from voiceai.modules.executions.runner import (  # noqa: F401
    history_to_transcript,
    merge_extracted_data,
    record_engine_execution,
)

__all__ = [
    "history_to_transcript",
    "merge_extracted_data",
    "record_engine_execution",
]