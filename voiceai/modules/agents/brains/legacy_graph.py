"""The legacy pre-preprocessed graph brain (spec 0002, A7): import parity only.

Behavior-preserving verbatim move of `voiceai/agent_types/graph_based_conversational_agent.py`.
This class is EXPORTED, NEVER CONSTRUCTED: the engine's star import
(task_manager.py:62) resolves ``GraphBasedConversationAgent`` but no dispatch path
instantiates it — the modern graph brain is :mod:`voiceai.modules.agents.brains.graph`.
The legacy quirks (the `print` stub, the error-swallowing generate) are preserved
verbatim because deleting or fixing dead legacy surface is its own cutover decision.
"""

from __future__ import annotations

import asyncio
import json
import random
import traceback
from typing import Any, Final

from voiceai.common.logger import get_logger
from voiceai.modules.agents.adapters.graph import get_md5_hash, update_prompt_with_context
from voiceai.modules.agents.brains.base import BaseAgent
from voiceai.modules.agents.constants import (
    MESSAGE_CONTENT_KEY,
    MESSAGE_ROLE_KEY,
    MODULE_NAME,
    SYSTEM_ROLE,
)

__all__ = ["Graph", "GraphBasedConversationAgent", "Node"]

logger = get_logger(MODULE_NAME)

# --- Conversation-data dict keys (the legacy preprocessed-graph contract) ---------------
_PROMPT_KEY: Final[str] = "prompt"
_LABEL_KEY: Final[str] = "label"
_CONTENT_KEY: Final[str] = "content"
_CLASSIFICATION_LABELS_KEY: Final[str] = "classification_labels"
_CHILDREN_KEY: Final[str] = "children"
_IS_ROOT_KEY: Final[str] = "is_root"
_MILESTONE_CHECK_PROMPT_KEY: Final[str] = "milestone_check_prompt"
_EXAMPLES_SEPARATOR: Final[str] = "###Examples"
_CLASSIFICATION_LABEL_KEY: Final[str] = "classification_label"
_TEXT_KEY: Final[str] = "text"
_AUDIO_KEY: Final[str] = "audio"
_END_OF_CONVERSATION: Final[str] = "<end_of_conversation>"
#: Legacy history windowing for the classification prompt.
_HISTORY_WINDOW_THRESHOLD: Final[int] = 7
_HISTORY_WINDOW: Final[int] = 6


class Node:
    """One node of the legacy preprocessed conversation graph.

    Args:
        node_id: The node's id in the conversation data.
        node_label: Human label, matched against classification results.
        content: The node's audio/text pairs.
        classification_labels: Labels the classification LLM may answer.
        prompt: The node's classification prompt.
        milestone_check_prompt: Auxiliary milestone prompt (unused downstream).
        children: Child nodes, wired after all nodes exist.
    """

    def __init__(
        self,
        node_id: Any,  # why: free-form legacy conversation-data ids
        node_label: Any,  # why: free-form legacy labels
        content: Any,  # why: free-form audio/text pair list
        classification_labels: list | None = None,
        prompt: Any = None,  # why: free-form legacy prompt payload
        milestone_check_prompt: Any = None,  # why: free-form legacy prompt payload
        children: list | None = None,
    ) -> None:
        self.node_id = node_id
        self.node_label = node_label
        self.content = content
        self.children = children
        self.classification_labels = classification_labels
        self.prompt = prompt
        self.need_response_generation = False
        self.milestone_check_prompt = milestone_check_prompt


class Graph:
    """The legacy preprocessed conversation graph: nodes wired parent-to-children."""

    def __init__(
        self,
        conversation_data: dict,
        preprocessed: bool = False,
        context_data: dict | None = None,
    ) -> None:
        self.preprocessed = preprocessed
        self.root: Node | None = None
        self.graph = self._create_graph(conversation_data, context_data)

    def _create_graph(self, data: dict, context_data: dict | None = None) -> dict:
        logger.info("Creating graph")
        node_map: dict[Any, Node] = dict()
        for node_id, node_data in data.items():
            prompt_parts = node_data.get(_PROMPT_KEY).split(_EXAMPLES_SEPARATOR)
            prompt = node_data.get(_PROMPT_KEY)
            if len(prompt_parts) == 2:
                classification_prompt = prompt_parts[0]
                user_prompt = update_prompt_with_context(prompt_parts[1], context_data)
                prompt = _EXAMPLES_SEPARATOR.join([classification_prompt, user_prompt])

            node = Node(
                node_id=node_id,
                node_label=node_data[_LABEL_KEY],
                content=node_data[_CONTENT_KEY],
                classification_labels=node_data.get(_CLASSIFICATION_LABELS_KEY, []),
                prompt=prompt,
                children=[],
                milestone_check_prompt=node_data.get(_MILESTONE_CHECK_PROMPT_KEY, ""),
            )
            node_map[node_id] = node
            if node_data[_IS_ROOT_KEY]:
                self.root = node

        for node_id, node_data in data.items():
            children_ids = node_data.get(_CHILDREN_KEY, [])
            node_map[node_id].children = [node_map[child_id] for child_id in children_ids] if children_ids else []
        return node_map

    # TODO(spec-0004): complete or delete this legacy stub at cutover — never called.
    def remove_node(self, parent: Node, node: Node) -> None:
        """Legacy unimplemented stub, preserved verbatim (never called)."""
        print("Not yet implemented")  # legacy-parity(spec-0002): dead stub, kept verbatim


class GraphBasedConversationAgent(BaseAgent):
    """The legacy preprocessed graph agent (import parity only — never constructed).

    Args:
        llm: The classification LLM (duck-typed legacy provider).
        prompts: Ignored by the legacy constructor (loaded later via
            :meth:`load_prompts_and_create_graph`), preserved verbatim.
        context_data: Context for prompt substitution.
        preprocessed: Whether the graph carries preprocessed audio pairs.
    """

    # why: legacy dead code assumes load_prompts_and_create_graph ran before use, so
    # the node cursors exist only after it (annotation-only keeps that AttributeError).
    current_node: Any
    current_node_interim: Any

    def __init__(
        self,
        llm: Any,  # why: duck-typed legacy provider seam
        prompts: Any,  # why: legacy dead parameter, preserved verbatim
        context_data: dict | None = None,
        preprocessed: bool = True,
    ) -> None:
        super().__init__()
        # Config
        self.llm = llm
        self.context_data = context_data
        self.preprocessed = preprocessed

        # Goals
        self.graph: Any = None  # why: legacy code reads .root without an Optional guard
        self.conversation_intro_done = False

    def load_prompts_and_create_graph(self, prompts: dict) -> None:
        """Build the graph from the prompts payload and set both node cursors to root."""
        self.graph = Graph(prompts, context_data=self.context_data)
        self.current_node = self.graph.root
        self.current_node_interim = self.graph.root  # Handle interim node because we are dealing with interim results

    def _get_audio_text_pair(self, node: Node) -> dict:
        ind = random.randint(0, len(node.content) - 1)  # noqa: S311 # why: playback variety, not crypto
        audio_pair: dict = node.content[ind]
        contextual_text = update_prompt_with_context(audio_pair[_TEXT_KEY], self.context_data)
        if contextual_text != audio_pair[_TEXT_KEY]:
            audio_pair[_TEXT_KEY] = contextual_text
            audio_pair[_AUDIO_KEY] = get_md5_hash(contextual_text)
        return audio_pair

    async def _get_next_formulaic_agent_next_step(
        self,
        history: list,
        stream: bool = True,
        synthesize: bool = False,
    ) -> Any:  # why: legacy unimplemented stub answers None; generate()'s dead branch iterates it
        pass

    def _handle_intro_message(self) -> dict:
        audio_pair = self._get_audio_text_pair(self.current_node)
        self.conversation_intro_done = True
        logger.info("Conversation intro done")
        if self.current_node.prompt is None:
            # These are convos with two step intros
            ind = random.randint(0, len(self.current_node.children) - 1)  # noqa: S311 # why: intro variety, not crypto
            self.current_node = self.current_node.children[ind]
        return audio_pair

    async def _get_next_preprocessed_step(self, history: list) -> dict | None:
        logger.info(
            f"current node {self.current_node.node_label}, self.current_node == self.graph.root "
            f"{self.current_node == self.graph.root}, and self.conversation_intro_done "
            f"{self.conversation_intro_done}"
        )
        if self.current_node == self.graph.root and not self.conversation_intro_done:
            return self._handle_intro_message()

        logger.info("Conversation intro was done and hence moving forward")
        if len(history) > _HISTORY_WINDOW_THRESHOLD:
            # TODO(spec-0004): add summary of left-out messages (legacy TODO, carried over)
            prev_messages = history[-_HISTORY_WINDOW:]
        else:
            prev_messages = history[1:]

        message = [{MESSAGE_ROLE_KEY: SYSTEM_ROLE, MESSAGE_CONTENT_KEY: self.current_node.prompt}] + prev_messages
        # Get classification label from LLM
        response = await self.llm.generate(message, request_json=True)
        logger.info(f"Classification response {response}")
        classification_result = json.loads(response)
        label = classification_result[_CLASSIFICATION_LABEL_KEY]
        for child in self.current_node.children:
            if child.node_label.strip().lower() == label.strip().lower():
                self.current_node_interim = child
                return self._get_audio_text_pair(child)
        return None  # legacy-parity(spec-0002): implicit None on no matching child

    def update_current_node(self) -> None:
        """Commit the interim node chosen from interim transcription results."""
        self.current_node = self.current_node_interim

    # Label flow is not being used right now as we're logging every request
    async def generate(self, history: list, label_flow: Any = None) -> Any:  # why: legacy loose stream contract
        """Yield the next preprocessed step (or the formulaic dead branch), swallowing errors."""
        try:
            if self.preprocessed:
                logger.info(f"Current node {str(self.current_node)}")
                if len(self.current_node.children) == 0:
                    ind = random.randint(0, len(self.current_node.content) - 1)  # noqa: S311 # why: variety, not crypto
                    audio_pair = self.current_node.content[ind]
                    logger.info(f"Agent: {audio_pair.get(_TEXT_KEY)}")
                    yield audio_pair
                else:
                    next_state = await self._get_next_preprocessed_step(history)
                    logger.info(f"Agent: {next_state}")
                    yield next_state

                if len(self.current_node.children) == 0:
                    await asyncio.sleep(1)
                    yield _END_OF_CONVERSATION
            else:
                # TODO(spec-0004): add non-preprocessed flow (legacy TODO, carried over)
                async for message in self._get_next_formulaic_agent_next_step(  # type: ignore[attr-defined] # why: legacy dead branch, crashes identically
                    history, True, False
                ):
                    yield message

        except Exception as e:  # legacy-parity(spec-0002): errors are logged and swallowed
            traceback.print_exc()  # legacy-parity(spec-0002): stderr traceback, kept verbatim
            logger.error(f"Error sending intro text: {e}")
