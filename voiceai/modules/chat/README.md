# chat README

Text-in/text-out conversations for chat-channel agents (spec 0038, Phase C).
Per-agent histories persist tenant-scoped (`chat_sessions`); the LLM turn
runner is injected (`ChatLlmPort`), defaulting to the shared completer.
