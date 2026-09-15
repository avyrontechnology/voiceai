"""Platform services: per-domain services (back-compat re-exports) (split from services.py; behavior frozen)."""

from voiceai.platform.services._shared import *  # noqa: F403,F401
from voiceai.platform.services.executions import *  # noqa: F403,F401
from voiceai.platform.services.batches import *  # noqa: F403,F401
from voiceai.platform.services.numbers import *  # noqa: F403,F401
from voiceai.platform.services.knowledge import *  # noqa: F403,F401
from voiceai.platform.services.tools import *  # noqa: F403,F401
from voiceai.platform.services.webhooks import *  # noqa: F403,F401
from voiceai.platform.services.wallet import *  # noqa: F403,F401
from voiceai.platform.services.templates import *  # noqa: F403,F401
from voiceai.platform.services.inbound_voices import *  # noqa: F403,F401
from voiceai.platform.services.accounts import *  # noqa: F403,F401
from voiceai.platform.services.integrations import *  # noqa: F403,F401
from voiceai.platform.services.graphs import *  # noqa: F403,F401
from voiceai.platform.services.workflows import *  # noqa: F403,F401
from voiceai.platform.services.campaigns import *  # noqa: F403,F401
from voiceai.platform.services.organization import *  # noqa: F403,F401

__all__ = [
    # from _shared
    "batch_registry",
    "call_registry",
    "get_batch_max_entries",
    "get_campaign_max_entries",
    "analytics_ttl",
    "cache_get",
    "cache_set",
    "invalidate_analytics_cache",
    # from executions
    "run_batch_background",
    "simulate_call",
    "list_executions",
    "get_execution_stats",
    "get_execution",
    "get_latency_stats",
    # from batches
    "create_batch",
    "list_batches",
    "get_batch",
    "start_batch",
    "get_batch_status",
    "stop_batch",
    "get_batch_executions",
    "retry_failed",
    "delete_batch",
    # from numbers
    "create_number",
    "list_numbers",
    "assign_number",
    "unassign_number",
    "delete_number",
    # from knowledge
    "create_kb",
    "list_kbs",
    "attach_kb",
    "detach_kb",
    "delete_kb",
    # from tools
    "create_tool",
    "list_tools",
    "delete_tool",
    # from webhooks
    "create_webhook",
    "list_webhooks",
    "delete_webhook",
    # from wallet
    "get_wallet",
    "topup_wallet",
    "get_ledger",
    # from templates
    "list_templates",
    "get_template_content",
    "import_template_content",
    # from inbound_voices
    "get_inbound",
    "put_inbound",
    "create_voice",
    "list_voices",
    "delete_voice",
    "get_vector_config",
    "put_vector_config",
    # from accounts
    "create_sub_account",
    "list_sub_accounts",
    "get_sub_account",
    "delete_sub_account",
    "add_member",
    "remove_member",
    # from integrations
    "create_integration",
    "list_integrations",
    "get_integration",
    "update_integration",
    "delete_integration",
    # from graphs
    "load_graph",
    "snapshot_graph_version",
    "create_graph",
    "list_graphs",
    "get_graph",
    "update_graph",
    "delete_graph",
    "list_graph_versions",
    "restore_graph_version",
    "validate_graph",
    "dry_run_graph",
    "deploy_graph",
    # from workflows
    "load_workflow",
    "snapshot_workflow_version",
    "create_workflow",
    "list_workflows",
    "get_workflow",
    "update_workflow",
    "delete_workflow",
    "list_workflow_versions",
    "restore_workflow_version",
    "validate_workflow_definition",
    "test_run_workflow",
    "get_workflow_run",
    # from campaigns
    "create_campaign",
    "list_campaigns",
    "get_campaign",
    "start_campaign",
    "stop_campaign",
    "get_campaign_runs",
    # from organization
    "get_organization",
    "update_organization",
    "reset_workspace",
    "create_api_key",
    "list_api_keys",
    "delete_api_key",
]
