# VoiceAI API Reference

Complete API Reference for Bolna Voice AI backend.

## Endpoints

### GET /agent/{agent_id}
**Get Agent Configuration**

Fetches an agent's complete configuration by its unique ID.

**Parameters:**
| Name | In | Required | Type | Description |
|------|----|----------|------|-------------|
| `agent_id` | path | Yes | string |  |


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Agent configuration successfully retrieved. | N/A |
| 404 | Agent not found. | [ErrorResponse](#errorresponse) |
| 500 | Internal server error. | [ErrorResponse](#errorresponse) |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### PUT /agent/{agent_id}
**Update Existing Agent**

Overwrites an existing agent's configuration. Recalculates extraction prompts if needed.

**Parameters:**
| Name | In | Required | Type | Description |
|------|----|----------|------|-------------|
| `agent_id` | path | Yes | string |  |


**Request Body:**
- Content-Type: `application/json`
- Schema: [CreateAgentPayload](#createagentpayload)


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Successful Response | [AgentUpdatedResponse](#agentupdatedresponse) |
| 404 | Agent not found. | [ErrorResponse](#errorresponse) |
| 500 | Internal server error. | [ErrorResponse](#errorresponse) |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### DELETE /agent/{agent_id}
**Delete Agent**

Removes an agent's configuration from the system by ID.

**Parameters:**
| Name | In | Required | Type | Description |
|------|----|----------|------|-------------|
| `agent_id` | path | Yes | string |  |


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Successful Response | [AgentDeletedResponse](#agentdeletedresponse) |
| 404 | Agent not found. | [ErrorResponse](#errorresponse) |
| 500 | Internal server error. | [ErrorResponse](#errorresponse) |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### POST /agent
**Create New Agent**

Creates a new agent configuration, generates an ID, and stores it in Redis. If extraction tasks are present, it will automatically generate extraction prompts.

**Request Body:**
- Content-Type: `application/json`
- Schema: [CreateAgentPayload](#createagentpayload)


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 201 | Successful Response | [AgentCreatedResponse](#agentcreatedresponse) |
| 500 | Internal server error. | [ErrorResponse](#errorresponse) |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### GET /all
**List All Agents**

Fetches all agents and their configurations currently stored in Redis.

**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Successful Response | [AgentListResponse](#agentlistresponse) |
| 500 | Internal server error. | [ErrorResponse](#errorresponse) |


### POST /twilio/call
**Initiate Outbound Call (Twilio)**

Initiates an outbound call using Twilio to the specified recipient phone number and connects it to the specified agent.

**Request Body:**
- Content-Type: `application/json`
- Schema: [CallDetails](#calldetails)


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Call initiated successfully. | N/A |
| 404 | Agent or recipient phone number not provided. | [ErrorResponse](#errorresponse) |
| 500 | Internal Server Error | [ErrorResponse](#errorresponse) |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### POST /twilio/twilio_connect
**Twilio TwiML Connect Callback**

Callback endpoint for Twilio to provide TwiML instructions for streaming audio to the VoiceAI WebSocket server.

**Parameters:**
| Name | In | Required | Type | Description |
|------|----|----------|------|-------------|
| `voiceai_host` | query | Yes | string | The public URL of the VoiceAI websocket host |
| `agent_id` | query | Yes | string | The ID of the agent to connect |


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | TwiML instructions returned successfully. | N/A |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### POST /plivo/call
**Initiate Outbound Call (Plivo)**

Initiates an outbound call using Plivo to the specified recipient phone number and connects it to the specified agent.

**Request Body:**
- Content-Type: `application/json`
- Schema: [CallDetails](#calldetails)


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Call initiated successfully. | N/A |
| 404 | Agent or recipient phone number not provided. | [ErrorResponse](#errorresponse) |
| 500 | Internal Server Error | [ErrorResponse](#errorresponse) |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### POST /plivo/plivo_connect
**Plivo Answer URL Callback**

Callback endpoint for Plivo to provide XML instructions for streaming audio to the VoiceAI WebSocket server.

**Parameters:**
| Name | In | Required | Type | Description |
|------|----|----------|------|-------------|
| `voiceai_host` | query | Yes | string | The public URL of the VoiceAI websocket host |
| `agent_id` | query | Yes | string | The ID of the agent to connect |


**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | XML instructions returned successfully. | N/A |
| 422 | Validation Error | [HTTPValidationError](#httpvalidationerror) |


### POST /plivo/plivo_hangup_callback
**Plivo Hangup Callback**

**Responses:**
| Status | Description | Schema |
|--------|-------------|--------|
| 200 | Successful Response | N/A |


## Schema Definitions

### APIParams
| Property | Type | Description |
|----------|------|-------------|
| `url` | string or null |  |
| `method` | string or null |  |
| `api_token` | string or null |  |
| `param` | string or object or null |  |
| `headers` | string or object or null |  |
| `pre_call_message` | string or object or null |  |
| `pre_call_webhook_url` | string or null |  |
| `pre_call_webhook_param` | string or object or null |  |
| `scope` | [ToolScope](#toolscope) or null |  |
| `nodes` | array or null |  |


### AgentCreatedResponse
| Property | Type | Description |
|----------|------|-------------|
| `agent_id` | string | The unique identifier for the created agent. |
| `state` | string | State of the agent creation. |


### AgentDeletedResponse
| Property | Type | Description |
|----------|------|-------------|
| `agent_id` | string | The unique identifier for the deleted agent. |
| `state` | string | State of the agent deletion. |


### AgentListItem
| Property | Type | Description |
|----------|------|-------------|
| `agent_id` | string | The ID of the agent. |
| `data` | object | The agent configuration data. |


### AgentListResponse
| Property | Type | Description |
|----------|------|-------------|
| `agents` | array of [AgentListItem](#agentlistitem) | List of all available agents. |


### AgentModel
| Property | Type | Description |
|----------|------|-------------|
| `agent_name` | string | A recognizable name for this agent. |
| `agent_type` | string | Type of agent architecture. E.g., 'other', 'graph_agent', 'llm_agent'. |
| `tasks` | array of [Task](#task) | List of tasks to execute in order. Can include conversations, extractions, etc. |
| `agent_welcome_message` | string or null | First message spoken by the agent upon connecting the call. |


### AgentRouteConfig
| Property | Type | Description |
|----------|------|-------------|
| `utterances` | array of string | Example utterances that should route to this agent. |
| `threshold` | number or null | Confidence threshold required to route. |


### AgentUpdatedResponse
| Property | Type | Description |
|----------|------|-------------|
| `agent_id` | string | The unique identifier for the updated agent. |
| `state` | string | State of the agent update. |


### AzureConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice` | string |  |
| `model` | string |  |
| `language` | string |  |
| `speed` | number or null |  |


### CartesiaConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `voice` | string |  |
| `model` | string |  |
| `language` | string |  |
| `speed` | number or null |  |


### ConversationConfig
| Property | Type | Description |
|----------|------|-------------|
| `optimize_latency` | boolean or null | Whether to aggressively optimize for lower latency across the pipeline. |
| `hangup_after_silence` | integer or null | Time in seconds of silence before the system automatically hangs up the call. |
| `incremental_delay` | integer or null | Incremental delay in milliseconds used to handle long pauses in conversation. |
| `number_of_words_for_interruption` | integer or null | Minimum number of words detected before triggering a barge-in/interruption. |
| `interruption_backoff_period` | integer or null | Time in milliseconds to ignore further audio immediately after an interruption. |
| `hangup_after_LLMCall` | boolean or null | Whether to automatically hang up after the LLM agent completes its primary goal. |
| `call_cancellation_prompt` | string or null | Prompt/instruction used to detect if the user wants to cancel or end the call. |
| `backchanneling` | boolean or null | Enable active listening/backchanneling (e.g., saying 'mm-hmm' while the user speaks). |
| `backchanneling_message_gap` | integer or null | Minimum gap in seconds between consecutive backchanneling messages. |
| `backchanneling_start_delay` | integer or null | Delay in seconds before initiating backchanneling behavior. |
| `ambient_noise` | boolean or null | Whether to play synthetic ambient noise in the background. |
| `call_terminate` | integer or null | Maximum total call duration in seconds before forced termination. |
| `use_fillers` | boolean or null | Whether to use filler words ('uh', 'um') before LLM responses to reduce perceived latency. |
| `trigger_user_online_message_after` | integer or null | Time in seconds of inactivity before prompting the user to see if they are still there. |
| `check_user_online_message` | string or object or null | The message played when checking if the user is still online. |
| `check_if_user_online` | boolean or null | Enable proactive checks to see if the user is still on the line. |
| `dtmf_enabled` | boolean or null | Whether to enable processing of DTMF (keypad) tones. |
| `voicemail` | boolean or null | Whether to enable voicemail detection. |
| `voicemail_detection_duration` | number or null | Time window in seconds to detect voicemail signals. |
| `voicemail_check_interval` | number or null | Minimum time in seconds between interim voicemail checks. |
| `voicemail_min_transcript_length` | integer or null | Minimum number of transcribed words to trigger an interim voicemail check. |


### CreateAgentPayload
| Property | Type | Description |
|----------|------|-------------|
| `agent_config` | [AgentModel](#agentmodel) | The main agent configuration including tools, tasks, and settings. |
| `agent_prompts` | object or null | Optional prompts mapped by intent/context. |


### DeepgramConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `voice` | string |  |
| `model` | string |  |


### Edge
| Property | Type | Description |
|----------|------|-------------|
| `start_node` | string |  |
| `end_node` | string |  |
| `condition` | array or null |  |


### EdgeConditionType
### ElevenLabsConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice` | string |  |
| `voice_id` | string |  |
| `model` | string |  |
| `temperature` | number or null |  |
| `similarity_boost` | number or null |  |
| `speed` | number or null |  |
| `style` | number or null |  |


### ErrorResponse
| Property | Type | Description |
|----------|------|-------------|
| `detail` | string | Error description message. |


### ExpressionCondition
| Property | Type | Description |
|----------|------|-------------|
| `variable` | string | Dot-notation key, e.g. 'detected_language' or 'recipient_data.timezone' |
| `operator` | [ExpressionOperator](#expressionoperator) | The operator to apply for the condition. |
| `value` | object or null | The value to compare against. |


### ExpressionGroup
| Property | Type | Description |
|----------|------|-------------|
| `logic` | [ExpressionLogic](#expressionlogic) | Logical operator (AND/OR) to combine multiple conditions. |
| `conditions` | array of [ExpressionCondition](#expressioncondition) | List of conditions to evaluate. |


### ExpressionLogic
### ExpressionOperator
### GeminiLiveConfig
| Property | Type | Description |
|----------|------|-------------|
| `model` | string | The Gemini Live model ID to use. |
| `voice` | string | The default voice to use. |
| `language` | string or null | Language setting. |
| `temperature` | number or null | Temperature for generation. |
| `start_sensitivity` | string or null | Voice activation start sensitivity. |
| `end_sensitivity` | string or null | Voice activation end sensitivity. |
| `vad_silence_duration_ms` | integer or null | VAD silence duration in ms. |
| `vad_prefix_padding_ms` | integer or null | Prefix padding for VAD. |
| `enable_session_resumption` | boolean | Whether to resume the session gracefully if it closes automatically. |
| `enable_context_compression` | boolean | Whether to compress context to save tokens over long sessions. |


### GraphEdge
Edge definition for graph-based conversation flow.

Each edge represents a possible transition from the current node.
The LLM will call the transition function when the condition is met.

| Property | Type | Description |
|----------|------|-------------|
| `to_node_id` | string | Target node ID to transition to. |
| `condition` | string | Human-readable description of when to transition. |
| `label` | string or null | Optional label for the edge. |
| `condition_type` | [EdgeConditionType](#edgeconditiontype) or null | Type of condition triggering the transition. None maps to 'llm'. |
| `expression` | [ExpressionGroup](#expressiongroup) or null | Expression to evaluate if condition_type is 'expression'. |
| `event_name` | string or null | Event name to match if condition_type is 'event'. |
| `function_name` | string or null | Name of the function the LLM must call to transition, e.g. 'go_to_city_question'. |
| `function_description` | string or null | Detailed description of the transition function for the LLM. |
| `parameters` | object or null | Optional parameters to collect during transition, mapping names to types. |
| `priority` | integer or null | Evaluation priority within the same condition type tier. Lower is evaluated first. |


### HTTPValidationError
| Property | Type | Description |
|----------|------|-------------|
| `detail` | array of [ValidationError](#validationerror) |  |


### IOModel
| Property | Type | Description |
|----------|------|-------------|
| `provider` | string |  |
| `format` | string or null |  |


### KalpaConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice` | string |  |
| `voice_id` | string or null |  |
| `model` | string |  |
| `temperature` | number or null |  |
| `acoustic_temperature` | number or null |  |
| `max_new_tokens` | integer or null |  |
| `audio_quality` | string or null |  |
| `chunk_length_schedule` | array or null |  |


### KnowledgeAgentConfig
| Property | Type | Description |
|----------|------|-------------|
| `model` | string or null | The primary LLM model used for generation. |
| `max_tokens` | integer or null | Maximum number of tokens to generate. |
| `family` | string or null | The family of the model (e.g., openai, anthropic). |
| `temperature` | number or null | Sampling temperature to control randomness. |
| `request_json` | boolean or null | Whether to enforce JSON output from the model. |
| `stop` | array or null | List of stop sequences. |
| `top_k` | integer or null | Top-K sampling parameter. |
| `top_p` | number or null | Top-P (nucleus) sampling parameter. |
| `min_p` | number or null | Min-P sampling parameter. |
| `frequency_penalty` | number or null | Penalty for frequent tokens. |
| `presence_penalty` | number or null | Penalty for new tokens based on presence. |
| `provider` | string or null | The LLM provider (e.g., openai, azure, groq). |
| `base_url` | string or null | Custom base URL for the LLM API. |
| `reasoning_effort` | [ReasoningEffort](#reasoningeffort) or null | Reasoning effort configuration for reasoning models (e.g., o1). |
| `verbosity` | [Verbosity](#verbosity) or null | Verbosity level of the LLM responses. |
| `use_responses_api` | boolean or null | Whether to use a specific responses API. |
| `compact_threshold` | integer or null | Threshold for compacting message history context. |
| `agent_information` | string or null | System prompt and instructions for the agent. |
| `prompt` | string or null | Additional context or base prompt. |
| `rag_config` | object or null | Configuration for RAG knowledge retrieval. |
| `llm_provider` | string or null | The LLM provider to use. |
| `context_data` | object or null | Context data injected into the agent's knowledge space. |


### KnowledgebaseAgent
| Property | Type | Description |
|----------|------|-------------|
| `model` | string or null | The model to use for the LLM. |
| `max_tokens` | integer or null | Maximum number of tokens to generate. |
| `family` | string or null | The family of the model (e.g., openai, anthropic). |
| `temperature` | number or null | Sampling temperature to control randomness. |
| `request_json` | boolean or null | Whether to enforce JSON output from the model. |
| `stop` | array or null | List of stop sequences. |
| `top_k` | integer or null | Top-K sampling parameter. |
| `top_p` | number or null | Top-P (nucleus) sampling parameter. |
| `min_p` | number or null | Min-P sampling parameter. |
| `frequency_penalty` | number or null | Penalty for frequent tokens. |
| `presence_penalty` | number or null | Penalty for new tokens based on presence. |
| `provider` | string or null | The provider to use for the LLM. |
| `base_url` | string or null | Custom base URL for the LLM API. |
| `reasoning_effort` | [ReasoningEffort](#reasoningeffort) or null | Reasoning effort configuration for reasoning models (e.g., o1). |
| `verbosity` | [Verbosity](#verbosity) or null | Verbosity level of the LLM responses. |
| `use_responses_api` | boolean or null | Whether to use a specific responses API. |
| `compact_threshold` | integer or null | Threshold for compacting message history context. |
| `vector_store` | [VectorStore](#vectorstore) | The vector store configuration to retrieve documents from. |


### LanceDBProviderConfig
| Property | Type | Description |
|----------|------|-------------|
| `vector_id` | string or null |  |
| `vector_ids` | array or null |  |
| `similarity_top_k` | integer or null |  |
| `score_threshold` | number or null |  |
| `reranker` | [RerankerConfig](#rerankerconfig) or null |  |


### Llm
| Property | Type | Description |
|----------|------|-------------|
| `model` | string or null | The primary LLM model used for generation. |
| `max_tokens` | integer or null | Maximum number of tokens to generate. |
| `family` | string or null | The family of the model (e.g., openai, anthropic). |
| `temperature` | number or null | Sampling temperature to control randomness. |
| `request_json` | boolean or null | Whether to enforce JSON output from the model. |
| `stop` | array or null | List of stop sequences. |
| `top_k` | integer or null | Top-K sampling parameter. |
| `top_p` | number or null | Top-P (nucleus) sampling parameter. |
| `min_p` | number or null | Min-P sampling parameter. |
| `frequency_penalty` | number or null | Penalty for frequent tokens. |
| `presence_penalty` | number or null | Penalty for new tokens based on presence. |
| `provider` | string or null | The LLM provider (e.g., openai, azure, groq). |
| `base_url` | string or null | Custom base URL for the LLM API. |
| `reasoning_effort` | [ReasoningEffort](#reasoningeffort) or null | Reasoning effort configuration for reasoning models (e.g., o1). |
| `verbosity` | [Verbosity](#verbosity) or null | Verbosity level of the LLM responses. |
| `use_responses_api` | boolean or null | Whether to use a specific responses API. |
| `compact_threshold` | integer or null | Threshold for compacting message history context. |


### LlmAgent
| Property | Type | Description |
|----------|------|-------------|
| `agent_flow_type` | string | The flow type, such as 'preprocessed'. |
| `agent_type` | string | The type of the agent: 'simple_llm_agent', 'graph_agent', 'multiagent', etc. |
| `llm_config` | [KnowledgebaseAgent](#knowledgebaseagent) or [LlmAgentGraph](#llmagentgraph) or [MultiAgent](#multiagent) or [SimpleLlmAgent](#simplellmagent) or [KnowledgeAgentConfig](#knowledgeagentconfig) | The detailed configuration specific to the agent_type. |


### LlmAgentGraph
| Property | Type | Description |
|----------|------|-------------|
| `nodes` | array of [Node](#node) |  |
| `edges` | array of [Edge](#edge) |  |


### MayaConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `voice` | string |  |
| `model` | string |  |
| `language` | string or null |  |


### MongoDBProviderConfig
| Property | Type | Description |
|----------|------|-------------|
| `connection_string` | string or null |  |
| `db_name` | string or null |  |
| `collection_name` | string or null |  |
| `index_name` | string or null |  |
| `llm_model` | string or null |  |
| `embedding_model` | string or null |  |
| `embedding_dimensions` | integer or null |  |


### MultiAgent
| Property | Type | Description |
|----------|------|-------------|
| `agent_map` | object | Map of agent names to their LLM configurations. |
| `agent_routing_config` | object | Routing logic configuration mapped by agent name. |
| `default_agent` | string | The name of the agent to route to by default. |
| `embedding_model` | string or null | Embedding model to use for intent routing matching. |


### Node
| Property | Type | Description |
|----------|------|-------------|
| `id` | string |  |
| `type` | string |  |
| `llm` | [Llm](#llm) |  |
| `exit_criteria` | string |  |
| `exit_response` | string or null |  |
| `exit_prompt` | string or null |  |
| `is_root` | boolean or null |  |


### NodeType
### OpenAIConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice` | string |  |
| `model` | string |  |


### OpenAIRealtimeConfig
| Property | Type | Description |
|----------|------|-------------|
| `model` | string | The OpenAI Realtime model ID to use. |
| `voice` | string | The default voice to use for audio generation. |
| `speed` | number or null | Playback rate of the generated audio (0.25 to 1.5). |
| `turn_detection_type` | string | Type of turn detection: 'semantic_vad' or 'server_vad'. |
| `eagerness` | string or null | How eagerly the model responds (auto, low, medium, high). |
| `vad_threshold` | number or null | VAD threshold (for server_vad only). |
| `vad_silence_duration_ms` | integer or null | Silence duration in ms before triggering VAD. |
| `vad_prefix_padding_ms` | integer or null | Prefix padding for VAD in ms. |
| `reasoning_effort` | [ReasoningEffort](#reasoningeffort) or null | Reasoning effort level (if supported by model). |
| `max_output_tokens` | integer or null | Maximum output tokens for generation. |
| `transcription_model` | string or null | Model used for transcribing input audio. |
| `language` | string or null | Language constraint for the session. |


### PixaConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `voice` | string |  |
| `model` | string |  |
| `language` | string |  |
| `top_p` | number or null |  |
| `repetition_penalty` | number or null |  |


### PollyConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice` | string |  |
| `engine` | string |  |
| `language` | string |  |


### ReasoningEffort
### RerankerConfig
Configuration for document reranking in RAG systems.

| Property | Type | Description |
|----------|------|-------------|
| `enabled` | boolean |  |
| `model_type` | string |  |
| `candidate_count` | integer |  |
| `final_count` | integer |  |


### RimeConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `language` | string |  |
| `voice` | string |  |
| `model` | string |  |


### S2SConfig
| Property | Type | Description |
|----------|------|-------------|
| `provider` | string | The S2S multimodal provider, e.g. 'openai_realtime' or 'gemini_live'. |
| `provider_config` | [OpenAIRealtimeConfig](#openairealtimeconfig) or [GeminiLiveConfig](#geminiliveconfig) | Configuration specific to the chosen S2S provider. |
| `welcome_audio_gate_ms` | integer | Milliseconds to suppress inbound audio at connection start to avoid VAD tripping on the agent's greeting. |


### SarvamConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `language` | string |  |
| `voice` | string |  |
| `model` | string |  |
| `speed` | number or null |  |


### SimpleLlmAgent
| Property | Type | Description |
|----------|------|-------------|
| `model` | string or null | The primary LLM model used for generation. |
| `max_tokens` | integer or null | Maximum number of tokens to generate. |
| `family` | string or null | The family of the model (e.g., openai, anthropic). |
| `temperature` | number or null | Sampling temperature to control randomness. |
| `request_json` | boolean or null | Whether to enforce JSON output from the model. |
| `stop` | array or null | List of stop sequences. |
| `top_k` | integer or null | Top-K sampling parameter. |
| `top_p` | number or null | Top-P (nucleus) sampling parameter. |
| `min_p` | number or null | Min-P sampling parameter. |
| `frequency_penalty` | number or null | Penalty for frequent tokens. |
| `presence_penalty` | number or null | Penalty for new tokens based on presence. |
| `provider` | string or null | The LLM provider (e.g., openai, azure, groq). |
| `base_url` | string or null | Custom base URL for the LLM API. |
| `reasoning_effort` | [ReasoningEffort](#reasoningeffort) or null | Reasoning effort configuration for reasoning models (e.g., o1). |
| `verbosity` | [Verbosity](#verbosity) or null | Verbosity level of the LLM responses. |
| `use_responses_api` | boolean or null | Whether to use a specific responses API. |
| `compact_threshold` | integer or null | Threshold for compacting message history context. |
| `agent_flow_type` | string or null |  |
| `extraction_details` | string or null |  |
| `summarization_details` | string or null |  |


### SmallestConfig
| Property | Type | Description |
|----------|------|-------------|
| `voice_id` | string |  |
| `language` | string |  |
| `voice` | string |  |
| `model` | string |  |


### Synthesizer
| Property | Type | Description |
|----------|------|-------------|
| `provider` | string | The text-to-speech provider to use (e.g., 'elevenlabs', 'polly', 'deepgram'). |
| `provider_config` | [PollyConfig](#pollyconfig) or [ElevenLabsConfig](#elevenlabsconfig) or [AzureConfig](#azureconfig) or [RimeConfig](#rimeconfig) or [SmallestConfig](#smallestconfig) or [SarvamConfig](#sarvamconfig) or [PixaConfig](#pixaconfig) or [CartesiaConfig](#cartesiaconfig) or [DeepgramConfig](#deepgramconfig) or [OpenAIConfig](#openaiconfig) or [MayaConfig](#mayaconfig) or [KalpaConfig](#kalpaconfig) | Provider-specific configuration details. |
| `stream` | boolean | Whether to stream synthesized audio back to the client. |
| `buffer_size` | integer or null | Buffer size in characters before sending text to the synthesizer. |
| `audio_format` | string or null | Audio format for the synthesized output. |
| `caching` | boolean or null | Enable caching of frequently synthesized phrases. |


### Task
| Property | Type | Description |
|----------|------|-------------|
| `tools_config` | [ToolsConfig](#toolsconfig) | Configuration mapping for tools, STT, TTS, and the LLM agent used in this task. |
| `toolchain` | [ToolsChainModel](#toolschainmodel) | Execution pipeline and chain for the tasks (e.g., parallel vs sequential). |
| `task_type` | string or null | Type of the task. E.g., 'conversation', 'extraction', 'summarization'. |
| `task_config` | [ConversationConfig](#conversationconfig) | Conversation settings, including latency optimizations and termination logic. |


### ToolDescription
| Property | Type | Description |
|----------|------|-------------|
| `type` | string | The type of the tool (usually 'function'). |
| `function` | [ToolFunction](#toolfunction) | The definition of the function. |


### ToolDescriptionLegacy
| Property | Type | Description |
|----------|------|-------------|
| `name` | string | The name of the tool function. |
| `description` | string | A description of what the tool does. |
| `parameters` | object | JSON Schema defining the expected parameters. |


### ToolFunction
| Property | Type | Description |
|----------|------|-------------|
| `name` | string | The name of the tool function. |
| `description` | string | A description of what the tool does. |
| `parameters` | object | JSON Schema defining the expected parameters. |
| `strict` | boolean | Whether to strictly enforce the parameter schema. |


### ToolModel
| Property | Type | Description |
|----------|------|-------------|
| `tools` | string or array or null | List of tool definitions or a string reference to tools. |
| `tools_params` | object | Configuration mapping for API endpoints these tools might call. |


### ToolScope
Where a graph-agent tool is exposed: GLOBAL (every node) or NODE (only its listed nodes).

### ToolsChainModel
| Property | Type | Description |
|----------|------|-------------|
| `execution` | string | Execution mode: 'parallel' or 'sequential'. |
| `pipelines` | array of array | A list of lists, where each sublist is a pipeline of tool names to execute. |


### ToolsConfig
| Property | Type | Description |
|----------|------|-------------|
| `llm_agent` | [LlmAgent](#llmagent) or [SimpleLlmAgent](#simplellmagent) or null | Configuration for the LLM agent responsible for understanding and responding to user intent. |
| `synthesizer` | [Synthesizer](#synthesizer) or null | Configuration for the Text-To-Speech (TTS) synthesizer. |
| `transcriber` | [Transcriber](#transcriber) or null | Configuration for the Speech-To-Text (STT) transcriber. |
| `input` | [IOModel](#iomodel) or null | Configuration for processing incoming audio streams. |
| `output` | [IOModel](#iomodel) or null | Configuration for processing outgoing audio streams. |
| `api_tools` | [ToolModel](#toolmodel) or null | External API tools that the LLM agent can call during the conversation. |
| `s2s` | [S2SConfig](#s2sconfig) or null | Configuration for server-to-server (S2S) multimodal audio providers (like OpenAI Realtime). |
| `switch_tool_description` | string or null | Description used when handing off to another agent in a multi-agent scenario. |
| `switch_handoff_messages` | object or null | Messages played to the user during agent handoffs, mapped by language/intent. |
| `agent_names` | object or null | Mapping of agent names for multi-agent dispatching. |


### Transcriber
| Property | Type | Description |
|----------|------|-------------|
| `model` | string or null | The transcriber model to use. |
| `language` | string or null | Language code for transcription (e.g., 'en', 'es'). |
| `stream` | boolean | Whether to stream audio data to the transcriber. |
| `sampling_rate` | integer or null | Audio sampling rate in Hz. |
| `encoding` | string or null | Audio encoding format. |
| `endpointing` | integer or null | Duration of silence in ms to trigger endpointing (utterance completion). |
| `keywords` | string or null | Comma-separated keywords to boost transcription accuracy. |
| `task` | string or null | Task type, usually 'transcribe'. |
| `provider` | string or null | The speech-to-text provider to use. |
| `multilingual` | object or null | Multilingual configuration settings. |
| `active` | string or null | Active status identifier. |
| `eot_threshold` | number or null | End-of-turn threshold for flux models. |
| `eager_eot_threshold` | number or null | Eager end-of-turn threshold. |
| `eot_timeout_ms` | integer or null | End-of-turn timeout in milliseconds. |
| `language_hints` | array or null | List of probable languages to hint the transcriber. |
| `delay` | string or null | Delay configuration ('low', 'medium', 'high'). |
| `noise_reduction` | boolean or null | Whether to apply noise reduction to the incoming audio. |
| `vad_threshold` | number or null | Voice Activity Detection (VAD) confidence threshold. |
| `vad_prefix_padding_ms` | integer or null | Padding in milliseconds applied before VAD triggers. |


### ValidationError
| Property | Type | Description |
|----------|------|-------------|
| `loc` | array of (string or integer) |  |
| `msg` | string |  |
| `type` | string |  |


### VectorStore
| Property | Type | Description |
|----------|------|-------------|
| `provider` | string |  |
| `provider_config` | [LanceDBProviderConfig](#lancedbproviderconfig) or [MongoDBProviderConfig](#mongodbproviderconfig) |  |


### Verbosity
### CallDetails
| Property | Type | Description |
|----------|------|-------------|
| `agent_id` | string | The ID of the agent to handle the call. |
| `recipient_phone_number` | string | The phone number to call in E.164 format (e.g., +1234567890). |

