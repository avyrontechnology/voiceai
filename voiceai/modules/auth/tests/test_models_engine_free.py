"""Engine-free canary: the auth models package must not drag engine code in (C2).

Runs in a fresh subprocess because this pytest process has long since imported the
engine through the legacy suite; only a clean interpreter can prove the import graph
is engine-free. Mirrors the agents A2 canary.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SUBPROCESS_TIMEOUT_S = 120

#: Engine namespaces the schema package must never load (agents-A2 canary list, mirrored).
FORBIDDEN_ENGINE_MODULES = (
    "voiceai.transcriber",
    "voiceai.synthesizer",
    "voiceai.input_handlers",
    "voiceai.output_handlers",
    "voiceai.s2s",
    "voiceai.providers",
)

CANARY_CODE = f"""
import sys
import voiceai.modules.auth.models
loaded = [m for m in {FORBIDDEN_ENGINE_MODULES!r} if m in sys.modules]
assert not loaded, f"engine modules imported by the models package: {{loaded}}"
"""


def test_models_package_imports_zero_engine_code():
    result = subprocess.run(
        [sys.executable, "-c", CANARY_CODE],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=SUBPROCESS_TIMEOUT_S,
        check=False,
    )
    assert result.returncode == 0, f"engine-free canary failed:\n{result.stderr}"
