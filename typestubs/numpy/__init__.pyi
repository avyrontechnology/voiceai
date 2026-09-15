"""Minimal shadow stub: installed numpy stubs use 3.12-only syntax.

Numpy values are typed as Any in checked code (audio utils treat
arrays opaquely). Remove when the toolchain targets 3.12+.
"""

from typing import Any

def __getattr__(name: str) -> Any: ...
