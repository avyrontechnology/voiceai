"""Public seam protocols for the platform module (contract E-01).

Stable import address for the seams other modules and the DI container
program against. Service functions keep their address at
``voiceai.platform.services`` (per-domain modules behind the package);
persistence keeps its address at ``voiceai.platform.repositories``.
"""

from voiceai.platform.repositories import PlatformRepository

__all__ = ["PlatformRepository"]
