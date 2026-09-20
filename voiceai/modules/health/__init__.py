"""Health module: probes this service's dependencies and exposes them over HTTP."""

from __future__ import annotations

from voiceai.modules import ModuleDef
from voiceai.modules.health.constants import MODULE_NAME
from voiceai.modules.health.controller import router

MODULE: ModuleDef = ModuleDef(name=MODULE_NAME, router=router)

__all__ = ["MODULE"]
