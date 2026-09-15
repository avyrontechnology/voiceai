"""Cross-module shared shapes: responses, pagination, datetime utilities.

This package MUST NOT import from ``voiceai.core`` or ``voiceai.database``.
Module code reuses what lives here and never redefines it.
"""

from __future__ import annotations
