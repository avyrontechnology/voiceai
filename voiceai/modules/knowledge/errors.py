"""Knowledge base errors (spec 0075)."""

from voiceai.common.errors import NotFoundError


class KnowledgeBaseNotFoundError(NotFoundError):
    """Raised when a requested knowledge base does not exist in the tenant."""

    def __init__(self, kb_id: str) -> None:
        super().__init__(f"Knowledge base not found: {kb_id}", details={"kb_id": kb_id})
