"""Pure, I/O-free webhook helpers (spec 0076)."""

from __future__ import annotations

from collections.abc import Sequence

from voiceai.modules.webhooks.models import Webhook

__all__ = ["for_agent", "is_active", "subscribes_to"]


def for_agent(webhooks: Sequence[Webhook], agent_id: str | None) -> list[Webhook]:
    """Return the hooks that apply to an agent.

    A hook with no `agent_id` is tenant-wide and applies to every agent, so it is
    included alongside the agent's own hooks.

    Args:
        webhooks: Hooks in repository order.
        agent_id: The agent to resolve for.

    Returns:
        The applicable hooks, in the input order.
    """
    return [hook for hook in webhooks if hook.agent_id in (None, agent_id)]


def is_active(webhook: Webhook) -> bool:
    """Report whether a hook is enabled.

    Args:
        webhook: The hook to test.

    Returns:
        True when the hook is enabled.
    """
    return webhook.enabled


def subscribes_to(webhook: Webhook, event: str) -> bool:
    """Report whether a hook subscribes to an event.

    An empty subscription list means "every event" — the legacy shape carries no
    such rule, but an empty list that matched nothing would make a freshly
    created hook silently inert.

    Args:
        webhook: The hook to test.
        event: The event name.

    Returns:
        True when the hook receives the event.
    """
    return not webhook.events or event in webhook.events
