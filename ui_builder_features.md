# Voice AI (Bolna) UI Builder Feature Specification

This document provides a highly detailed breakdown of all configurable entities, properties, and settings exposed by the Bolna Voice AI backend. If you are building a UI (like a node-based graph builder, or a settings dashboard) for this framework, these are the models, enums, and properties you need to expose to the user.

---

## 1. Top-Level Agent Settings (`AgentModel`)
Every agent created in the UI should map to this base structure:
- **`agent_name`**: (String) Name of the agent.
- **`agent_type`**: (String) e.g., "other", "graph_agent", "knowledgebase_agent", etc.
- **`agent_welcome_message`**: (String) The first message the agent says when a call connects.
- **`tasks`**: (Array) List of `Task` configurations that run sequentially or in parallel.

### 1.1 Task Configuration (`Task`)
- **`task_type`**: (String) "conversation", "extraction", "summarization", "notification".
- **`toolchain`**: 
  - `execution`: "parallel" or "sequential".
  - `pipelines`: Matrix of execution pipelines (e.g., `[["transcriber", "llm", "synthesizer"]]`).
- **`tools_config`**: Connects the Transcriber, Synthesizer, LLM, and Input/Output telephony.
- **`task_config`**: (`ConversationConfig`) Detailed call behavior settings (see Section 6).

---

## 2. Transcriber Settings (STT / ASR)
Settings for configuring how the system listens to the user.

**Supported Providers**: `deepgram`, `azure`, `sarvam`, `assembly`, `google`, `pixa`, `gladia`, `elevenlabs`, `smallest`, `openai`, `soniox`, `gemini`.

**Global Transcriber Properties**:
- `model`: (String) e.g., "nova-2".
- `language`: (String) Language code.
- `stream`: (Boolean) Enable WebSockets/streaming mode.
- `sampling_rate`: (Integer) Default `16000`.
- `encoding`: (String) Default `"linear16"`.
- `endpointing`: (Integer) Silence duration to detect end of speech (e.g., 500ms).
- `keywords`: (String) Custom vocabulary or boosting.
- `noise_reduction`: (Boolean) Enable background noise filtering.

**Advanced VAD & EOT (End of Turn) Properties**:
- `vad_threshold`: (Float) Default `0.5`.
- `vad_prefix_padding_ms`: (Integer) Default `300`.
- `eot_threshold`, `eager_eot_threshold`, `eot_timeout_ms`.

---

## 3. Synthesizer Settings (TTS)
Settings for configuring how the agent sounds.
**Global Synthesizer Properties**:
- `stream`: (Boolean)
- `buffer_size`: (Integer) Number of characters to buffer before generating audio (Default: 40).
- `audio_format`: (String) "pcm", "mp3", "wav".
- `caching`: (Boolean) Cache generated TTS audio to save costs and reduce latency.

**Provider-Specific Configurations** (UI should show these conditionally based on provider):
* **ElevenLabs**: `voice`, `voice_id`, `model`, `temperature`, `similarity_boost`, `speed`, `style`.
* **AWS Polly**: `voice`, `engine`, `language`.
* **Deepgram / OpenAI**: `voice_id`, `voice`, `model`.
* **Cartesia**: `voice_id`, `voice`, `model`, `language`, `speed`.
* **Pixa**: `voice_id`, `voice`, `model`, `language`, `top_p`, `repetition_penalty`.
* **Maya**: `voice_id` ("Ananya" or "Arjun"), `voice`, `model`, `language` ("auto", "en", "hi", etc.).
* **Kalpa**: `voice`, `model`, `temperature`, `acoustic_temperature`, `audio_quality`, `max_new_tokens`.

---

## 4. LLM & Agent Core Logic
Settings for configuring the "brain" of the agent.

**Global LLM Properties**:
- `provider`: (String) "openai", "azure", "gemini", or "litellm" (supporting Anthropic, Groq, DeepSeek, etc.).
- `model`: (String) Model ID (e.g., "gpt-4o").
- `max_tokens`: (Integer)
- `temperature`: (Float)
- `top_k`, `top_p`, `min_p`, `frequency_penalty`, `presence_penalty`.
- `request_json`: (Boolean) Force JSON output.
- `reasoning_effort`: (Enum) "low", "medium", "high" (For reasoning models like o1/o3-mini).

### 4.1 Graph Agent UI (`GraphAgentConfig`)
If building a node-based flow builder UI, these are the core properties:
- **`agent_information`**: (String) System prompt describing the agent's persona.
- **`nodes`**: (Array of `GraphNode`)
  - `id`: (String) Node identifier.
  - `node_type`: (Enum) `llm` or `router`. (Routers never speak, they just branch).
  - `prompt`: (String) Context specific to this node.
  - `static_message`: (String/Dict) Fixed text to say without using the LLM.
  - `repeat_after_silence_seconds`: (Float) Trigger for re-prompting.
  - `edges`: (Array of transitions)
    - `to_node_id`: (String) Destination.
    - `condition_type`: (Enum) "expression", "event", "llm", "unconditional".
    - `priority`: (Integer) Routing priority.
- **`routing_model`**: (String) Separate fast LLM (e.g., Groq) just for decision-making routing.

---

## 5. RAG & Knowledge Base (`RagConfig`)
Settings for connecting documents or databases to the agent.

**Vector Store Providers**:
* **MongoDB**: `connection_string`, `db_name`, `collection_name`, `index_name`, `embedding_model`, `embedding_dimensions`.
* **LanceDB**: `vector_id`, `similarity_top_k`, `score_threshold`.

**Reranker Configuration** (UI should have a toggle for Reranking):
- `enabled`: (Boolean)
- `model_type`: (String) e.g., "minilm-l6-v2", "bge-large".
- `candidate_count`: (Integer) Retrieve X documents (e.g., 20).
- `final_count`: (Integer) Return top Y reranked documents (e.g., 5).

---

## 6. Advanced Call Behavior (`ConversationConfig`)
This section maps to "Advanced Settings" or "Call Settings" in a UI:

**General Flow & Latency**:
- `optimize_latency`: (Boolean)
- `incremental_delay`: (Integer in ms) Delay to handle long pauses.
- `ambient_noise`: (Boolean) Play background noise during silence.
- `use_fillers`: (Boolean) Play "Umm", "Ah" while LLM thinks.
- `backchanneling`: (Boolean) Play "Uh-huh", "Yeah" while user speaks.

**Interruption & Silence (VAD tunings)**:
- `hangup_after_silence`: (Integer in seconds)
- `number_of_words_for_interruption`: (Integer) Minimum words user must say to trigger a barge-in/interruption.
- `interruption_backoff_period`: (Integer) Wait time after user stops before agent resumes.

**User Engagement (Liveness checks)**:
- `check_if_user_online`: (Boolean)
- `trigger_user_online_message_after`: (Integer in seconds)
- `check_user_online_message`: (String) e.g., "Hey, are you still there?"

**Voicemail Detection (AMD)**:
- `voicemail`: (Boolean) Enable Answering Machine Detection.
- `voicemail_detection_duration`: (Float) Time window in seconds.
- `voicemail_check_interval`: (Float) Min time between interim checks.
- `voicemail_min_transcript_length`: (Integer) Min words to analyze.

---

## 7. Speech-to-Speech (S2S) Modalities
If skipping discrete ASR/TTS and using true multi-modal models:

**OpenAI Realtime (`OpenAIRealtimeConfig`)**:
- `model`, `voice`, `speed`
- `turn_detection_type`: "server_vad" or "semantic_vad" (waits for sentences to complete).
- `eagerness`: "auto", "low", "medium", "high".

**Gemini Live (`GeminiLiveConfig`)**:
- `start_sensitivity`, `end_sensitivity`
- `vad_silence_duration_ms` (Recommended: 600ms)
- `enable_session_resumption`, `enable_context_compression`.

---

## 8. Function Calling & Tools (`ToolModel`)
To build a tool/function registry in the UI:
- **`name`**: Tool name.
- **`description`**: What the tool does.
- **`parameters`**: (JSON Schema) Define arguments (e.g., {"type": "object", "properties": {...}}).
- **`strict`**: (Boolean) Force structured output strictness.
