"""Pure utilities for the platform submodule.

Stateless helpers with no store, principal, or I/O: statistics math,
time-window cutoffs, and definition parsing. Used by services.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from voiceai.platform.exceptions import HTTPException
from voiceai.platform.graphs import GraphDefinition
from voiceai.platform.workflows import WorkflowDefinition


def percentile(sorted_values: list, pct: float) -> Optional[int]:
    """Return the pct-th percentile of pre-sorted values.

    Args:
        sorted_values: Values in ascending order.
        pct: Percentile in 0..100.

    Returns:
        The percentile value, or None when empty.
    """
    if not sorted_values:
        return None
    index = min(int(pct / 100 * len(sorted_values)), len(sorted_values) - 1)
    return sorted_values[index]


def cutoff_for_days(days: Optional[int]) -> Optional[datetime]:
    """Return the UTC cutoff for a trailing-day window.

    Args:
        days: Window length; None means all time.

    Returns:
        The cutoff datetime, or None for unbounded windows.
    """
    if days is None:
        return None
    from datetime import timedelta

    return datetime.now(timezone.utc) - timedelta(days=days)


def parse_graph_definition(raw: dict) -> GraphDefinition:
    """Validate raw graph JSON into a GraphDefinition.

    Args:
        raw: The stored definition payload.

    Returns:
        The validated definition.

    Raises:
        HTTPException: 422 with field-level details when invalid.
    """
    from pydantic import ValidationError as PydanticValidationError

    try:
        return GraphDefinition(**raw)
    except PydanticValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        raise HTTPException(status_code=422, detail=f"Invalid graph definition: {details}")


def parse_workflow_definition(raw: dict) -> WorkflowDefinition:
    """Validate raw workflow JSON into a WorkflowDefinition.

    Args:
        raw: The stored definition payload.

    Returns:
        The validated definition.

    Raises:
        HTTPException: 422 with field-level details when invalid.
    """
    from pydantic import ValidationError as PydanticValidationError

    try:
        return WorkflowDefinition(**raw)
    except PydanticValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        raise HTTPException(status_code=422, detail=f"Invalid workflow definition: {details}")


def latency_buckets(fresh_raw: List[dict]) -> dict:
    """Bucket raw latency rows by calendar day.

    Args:
        fresh_raw: Raw latency rows from the store aggregate.

    Returns:
        Mapping of ISO day to end-to-end millisecond samples.
    """
    buckets: dict = {}
    for raw in fresh_raw:
        try:
            started_raw = raw.get("started_at")
            if isinstance(started_raw, str):
                started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            else:
                continue
            day = started.date().isoformat()
        except Exception:
            continue
        bucket = buckets.setdefault(day, [])
        latency = raw.get("latency") or {}
        val = latency.get("e2e_ms") if isinstance(latency, dict) else None
        if val is not None:
            bucket.append(val)
    return buckets
