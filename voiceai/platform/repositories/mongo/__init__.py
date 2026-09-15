"""MongoDB backend subpackage (contract L-03, V-04).

``MongoStore`` composes per-collection-group mixins; behavior identical
to the former ``mongo_store.py``. Reporting keys live in ``core``.
"""

from voiceai.platform.repositories.mongo.core import COLLECTION_KEY_BY_MODEL
from voiceai.platform.repositories.mongo.core import MongoCore
from voiceai.platform.repositories.mongo.executions import ExecutionMixin
from voiceai.platform.repositories.mongo.batches import BatchMixin
from voiceai.platform.repositories.mongo.numbers import NumberMixin
from voiceai.platform.repositories.mongo.knowledge import KnowledgeMixin
from voiceai.platform.repositories.mongo.tools import ToolMixin
from voiceai.platform.repositories.mongo.webhooks import WebhookMixin
from voiceai.platform.repositories.mongo.telephony import TelephonyMixin
from voiceai.platform.repositories.mongo.accounts import AccountMixin
from voiceai.platform.repositories.mongo.integrations import IntegrationMixin
from voiceai.platform.repositories.mongo.graphs import GraphMixin
from voiceai.platform.repositories.mongo.workflows import WorkflowMixin
from voiceai.platform.repositories.mongo.campaigns import CampaignMixin
from voiceai.platform.repositories.mongo.identity import IdentityMixin
from voiceai.platform.repositories.mongo.wallet import WalletMixin


class MongoStore(
    MongoCore,
    ExecutionMixin,
    BatchMixin,
    NumberMixin,
    KnowledgeMixin,
    ToolMixin,
    WebhookMixin,
    TelephonyMixin,
    AccountMixin,
    IntegrationMixin,
    GraphMixin,
    WorkflowMixin,
    CampaignMixin,
    IdentityMixin,
    WalletMixin,
):
    """Beanie-backed PlatformRepository (mixin composition; see submodules)."""


__all__ = ["COLLECTION_KEY_BY_MODEL", "MongoStore"]
