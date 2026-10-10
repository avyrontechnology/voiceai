"""Pure, I/O-free graph versioning helpers (spec 0078).

The numbering rule here is a data-integrity contract, not a convenience: version
numbers are what `POST /{id}/restore/{n}` addresses, so re-numbering them would
make an existing restore URL point at a different snapshot forever.
"""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.automation.constants import RESTORE_NOTE_TEMPLATE
from voiceai.modules.automation.models import GraphVersion

__all__ = ["find_version", "next_version_number", "ordered", "restore_note"]


def next_version_number(versions: Sequence[GraphVersion]) -> int:
    """Return the version number a new snapshot should take.

    `len(versions) + 1`, exactly as the legacy store computed it. It counts
    rather than taking `max + 1` on purpose: that is the historical behaviour,
    and matching it is what keeps numbering identical after the migration.

    Args:
        versions: The graph's existing snapshots, in any order.

    Returns:
        The number to assign the next snapshot.
    """
    return len(versions) + 1


def ordered(versions: Sequence[GraphVersion]) -> list[GraphVersion]:
    """Return snapshots in version order, oldest first.

    Args:
        versions: The graph's snapshots, in any order.

    Returns:
        A new list sorted by `version_number`.
    """
    return sorted(versions, key=lambda version: version.version_number)


def find_version(versions: Sequence[GraphVersion], version_number: int) -> GraphVersion | None:
    """Find one snapshot by its version number.

    Args:
        versions: The graph's snapshots.
        version_number: The number to look for.

    Returns:
        The matching snapshot, or `None` when the number was never issued.
    """
    return next((version for version in versions if version.version_number == version_number), None)


def restore_note(version_number: int) -> str:
    """Build the snapshot note written when restoring a version.

    Args:
        version_number: The version being restored.

    Returns:
        The note stored on the snapshot taken just before the restore.
    """
    return RESTORE_NOTE_TEMPLATE.format(version_number=version_number)
