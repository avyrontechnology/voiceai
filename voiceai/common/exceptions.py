"""Module throw-surface for shared shapes.

``common`` raises no errors of its own: builders take validated inputs and
signal misuse with builtin ``ValueError``. This module exists so the
package shape stays uniform; it intentionally exports nothing.
"""

from __future__ import annotations

__all__: list[str] = []
