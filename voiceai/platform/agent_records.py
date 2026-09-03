"""Helpers for listing agent records from the shared Redis.

Agent configs live under bare UUID keys while platform data uses
`platform:v1:`-prefixed keys (plus index sets). The legacy `/all`
endpoint scans `KEYS *`, so without filtering it returns platform
records as fake agents — and its positional zip of keys to values
misaligns IDs as soon as any key is skipped. These pure helpers keep
that logic testable without importing the engine.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from voiceai.helpers.logger_config import configure_logger

logger = configure_logger(__name__)


def is_agent_key(key: str) -> bool:
    """Bare UUID agent keys never contain a colon; namespaced data always does."""
    return ":" not in key


def parse_agent_record(key: str, raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return {"agent_id", "data"} for genuine agent records, else None."""
    if not is_agent_key(key) or not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(f"Skipping unreadable agent record {key}: {exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        return None
    return {"agent_id": key, "data": data}


def collect_agent_records(pairs: List[Tuple[str, Optional[str]]]) -> List[Dict[str, Any]]:
    """Build the /all payload from (key, raw value) pairs, preserving IDs."""
    records = []
    for key, raw in pairs:
        record = parse_agent_record(key, raw)
        if record is not None:
            records.append(record)
    return records
