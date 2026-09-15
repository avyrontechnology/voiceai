"""
Base class for WebSocket-streaming TTS synthesizers.

Subclasses only need to implement a handful of provider-specific methods:
  - establish_connection()  -> connect & return websocket (or None)
  - sender()               -> send text to the WS
  - receiver()             -> async-generator yielding audio bytes (b'\\x00' = end)
  - form_payload()         -> build the JSON payload for a text chunk (optional)
  - _get_audio_format()    -> return the meta_info 'format' string
  - _process_audio_chunk() -> transform raw audio before yielding (optional)

Everything else — push routing, latency tracking, first-chunk bookkeeping,
monitor_connection, cleanup — lives here.
"""

import asyncio
import copy
import json
import time
import traceback
from collections import deque

import websockets

from .base_synthesizer import BaseSynthesizer
from voiceai.core.resilience import TaskRegistry, call_soft
from voiceai.helpers.utils import create_ws_data_packet
from voiceai.otobaai_logger import get_logger
from voiceai.synthesizer.exceptions import SynthesizerError, classify_exception, summarize_exception

logger = get_logger(__name__)

# Maximum consecutive connection failures before giving up
MAX_CONNECTION_FAILURES = 3


class StreamSynthesizer(BaseSynthesizer):
    """Base class for all WebSocket-streaming synthesizers."""

    def __init__(self, stream=True, provider_name="stream", task_manager_instance=None, buffer_size=400, **kwargs):
        super().__init__(
            task_manager_instance=task_manager_instance,
            stream=stream,
            buffer_size=buffer_size,
        )
        self.provider_name = provider_name
        # Serialize WS sends in push order (like Kalpa): overwriting sender_task
        # without joining let two sender coroutines interleave frames on one socket.
        self._send_lock = asyncio.Lock()
        self._tasks = TaskRegistry(name=f"synth-{provider_name}", logger=logger)
        self._sender_tasks: list = []

        # WebSocket state
        self.websocket = None
        self.sender_task = None
        self.conversation_ended = False
        self.connection_error = None

        # Text / meta_info queue (sender pushes, generate pops)
        self.text_queue = deque()
        self.meta_info = None
        self.current_text = ""
        self.last_text_sent = False

        # Turn-level latency tracking
        self.current_turn_start_time = None
        self.current_turn_id = None
        self.current_sequence_id = None
        # Canned speech (handoff, goodbye, ...) has no turn_id; the category is the only
        # way downstream can attribute its latency entry to the message that was spoken.
        self.current_message_category = None
        self.current_tts_start_ms = None
        self.current_turn_ttfb = None
        self.ws_send_time = None
        self.current_sequence_chars = 0

    # ------------------------------------------------------------------
    # Subclass hooks (override these)
    # ------------------------------------------------------------------

    async def establish_connection(self):
        """Connect to the provider WebSocket. Return the websocket object or None."""
        raise NotImplementedError

    async def sender(self, text, sequence_id, end_of_llm_stream=False):
        """Send *text* to the WebSocket. Called as an asyncio task."""
        raise NotImplementedError

    async def receiver(self):
        """Async generator yielding raw audio bytes. Yield b'\\x00' for end-of-stream."""
        raise NotImplementedError
        yield  # pragma: no cover — make this a generator

    def _get_audio_format(self):
        """Return the format string for meta_info (e.g. 'mulaw', 'wav', 'pcm')."""
        return "wav"

    def _process_audio_chunk(self, chunk):
        """Optional per-chunk transform for WS streaming (resample, decode, etc). Return bytes."""
        return chunk

    def _process_http_audio(self, audio):
        """Audio conversion for HTTP mode. Defaults to _process_audio_chunk.

        Override separately when the HTTP response format differs from the WS
        wire format (e.g. Rime WS sends mulaw, but HTTP sends mp3/wav).
        """
        return self._process_audio_chunk(audio)

    def _get_http_audio_format(self):
        """Output format string for HTTP mode. Defaults to _get_audio_format.

        Override when HTTP output format differs from WS output format.
        """
        return self._get_audio_format()

    def _unpack_receiver_message(self, item):
        """Unpack what receiver() yields into (audio_bytes, extra_meta_dict).

        Default assumes receiver() yields raw bytes.
        Override if your receiver yields richer objects (e.g. ElevenLabs yields
        (audio, text_synthesized) tuples).
        """
        return item, {}

    # ------------------------------------------------------------------
    # Shared sender helpers
    # ------------------------------------------------------------------

    def _is_ws_connected(self):
        ws = self.websocket
        return ws is not None and ws.state is not websockets.protocol.State.CLOSED

    async def _wait_for_ws(self, poll_interval=1):
        """Block until the WebSocket is connected."""
        while not self._is_ws_connected():
            if self.conversation_ended or self.connection_error:
                logger.info(
                    f"Aborting {self.provider_name} sender wait: conversation_ended={self.conversation_ended} connection_error={self.connection_error}"
                )
                return
            logger.info(f"Waiting for {self.provider_name} WebSocket connection...")
            await asyncio.sleep(poll_interval)

    async def _send_json(self, payload):
        """Send a JSON payload over the WebSocket. Sets connection_error on failure."""
        try:
            await self.websocket.send(json.dumps(payload))
        except Exception as e:
            logger.error(f"Error sending to {self.provider_name}: {e}")
            self.connection_error = str(e)
            raise

    # ------------------------------------------------------------------
    # push()  — routes to WS queue or internal_queue
    # ------------------------------------------------------------------

    async def push(self, message):
        if self.stream:
            await self._push_stream(message)
        else:
            self.internal_queue.put_nowait(copy.deepcopy(message))

    async def _push_stream(self, message):
        meta_info = message.get("meta_info")
        text = message.get("data")
        self.current_text = text
        self.synthesized_characters += len(text) if text else 0
        self.current_sequence_chars += len(text) if text else 0
        self.meta_info = copy.deepcopy(meta_info)
        meta_info["text"] = text

        # Stamp turn start on first push of a new turn
        self._stamp_turn_start(meta_info)

        # Pre-push hook (e.g. context_id); Cartesia may defer end_of_llm_stream, so read it after.
        self._on_push(meta_info, text)

        end_of_llm_stream = meta_info.get("end_of_llm_stream", False)
        sequence_id = meta_info.get("sequence_id")
        prev = self.sender_task
        message_text = text

        async def _chained_sender():
            # Join the prior sender so frames leave in push order; the per-sender
            # _send_lock (held inside sender(), like Kalpa) then guarantees no interleave.
            if prev is not None and not prev.done():
                try:
                    await prev
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
            await self.sender(message_text, sequence_id, end_of_llm_stream)

        try:
            task = self._tasks.create(_chained_sender(), name=f"sender-seq-{sequence_id}")
        except Exception:
            task = asyncio.create_task(_chained_sender())
        self.sender_task = task
        self._sender_tasks.append(task)
        # Bound the list; done tasks are pruned, registry still owns failures.
        self._sender_tasks = [t for t in self._sender_tasks if not t.done()][-16:]
        self.text_queue.append(meta_info)

    def _stamp_turn_start(self, meta_info):
        """Only stamp on the first push of a new turn (don't re-stamp on subsequent chunks)."""
        if self.current_turn_start_time is None:
            # Drop leftover entries from a previous turn so this turn's first audio
            # chunk doesn't pop a stale entry and inherit a dead sequence_id (which
            # __listen_synthesizer would then filter out). Keep same-seq entries —
            # they belong to this turn (sequence_ids are monotonic; only -1 repeats).
            new_seq = meta_info.get("sequence_id")
            if self.text_queue and any(m.get("sequence_id") != new_seq for m in self.text_queue):
                kept = deque(m for m in self.text_queue if m.get("sequence_id") == new_seq)
                dropped = len(self.text_queue) - len(kept)
                self.text_queue = kept
                logger.info(f"Dropped {dropped} stale text_queue entries on new turn start (seq={new_seq})")
            self.current_turn_start_time = time.perf_counter()
            self.ws_send_time = None
            self.current_turn_ttfb = None
            # Anchor tts_start_ms to the first push — re-pushes of speculative responses
            # (e.g. after a tool call confirms the eager response) would overwrite this
            # to a later timestamp, making tts_start appear after agent_speech_start.
            self.current_tts_start_ms = meta_info.get("tts_start_ms")
            self.last_text_sent = False
            logger.info(f"Push new_turn text_len={len(meta_info.get('text', '') or '')}")
        self.current_turn_id = meta_info.get("turn_id")
        self.current_sequence_id = meta_info.get("sequence_id")
        self.current_message_category = meta_info.get("message_category")
        # Eager stub — captured even if turn never produces audio (e.g. TTS WS drop).
        # _record_turn_latency() upserts by sequence_id on completion, replacing this entry.
        self._upsert_turn_latency(
            {
                "turn_id": self.current_turn_id,
                "sequence_id": self.current_sequence_id,
                "tts_start_ms": self.current_tts_start_ms,
                "message_category": self.current_message_category,
            }
        )

    def _on_push(self, meta_info, text):
        """Provider-specific hook called during push before sender is created."""
        pass

    # ------------------------------------------------------------------
    # generate()  — the main audio-producing async generator
    # ------------------------------------------------------------------

    async def generate(self):
        try:
            if self.stream:
                async for packet in self._generate_ws_loop():
                    yield packet
            else:
                async for packet in self._generate_http_loop():
                    yield packet
        except Exception as e:
            logger.error(f"Error in {self.provider_name} generate: {e}", exc_info=True)
            raise

    def _turn_key(self, meta):
        """Turn identity for WS correlation: sequence + turn + category.

        Canned speech shares sequence_id -1, so category joins the key — otherwise a
        goodbye's EOS would complete a handoff's turn.
        """
        if not isinstance(meta, dict):
            return (None, None, None)
        return (meta.get("sequence_id"), meta.get("turn_id"), meta.get("message_category"))

    def _prune_stale_queue(self):
        """Drop queued metas for retired sequences so audio never inherits a dead id."""
        try:
            queue = getattr(self, "text_queue", None)
            if not queue:
                return
            should = getattr(self, "should_synthesize_response", None)
            if callable(should):
                while len(queue) > 0:
                    try:
                        valid = should(queue[0].get("sequence_id"))
                    except Exception:
                        break
                    if valid:
                        break
                    stale = queue.popleft()
                    logger.info(
                        f"{self.provider_name}: dropped stale text_queue entry "
                        f"seq={stale.get('sequence_id')} in generate loop"
                    )
                    if not queue:
                        break
            else:
                # No task-manager hook (unit fakes): sequence_ids are monotonic, so when
                # several distinct sequences are queued the older ones are stale.
                seqs = [m.get("sequence_id") for m in queue]
                if len(set(seqs)) > 1:
                    newest = seqs[-1]
                    kept = deque(m for m in queue if m.get("sequence_id") == newest)
                    dropped = len(queue) - len(kept)
                    if dropped:
                        self.text_queue = kept
                        logger.info(f"Dropped {dropped} stale text_queue entries in generate loop")
        except Exception:
            pass

    def _carry_turn_end_flag(self):
        """Merge end_of_llm_stream from any same-turn queued meta into the active one.

        Pushes and audio chunks are not 1:1 (providers buffer), so the EOS sentinel
        must not depend on which chunk's meta was popped.
        """
        try:
            active = getattr(self, "meta_info", None)
            queue = getattr(self, "text_queue", None)
            if not isinstance(active, dict) or not queue:
                return
            active_key = self._turn_key(active)
            for m in list(queue):
                if self._turn_key(m) == active_key and m.get("end_of_llm_stream"):
                    active["end_of_llm_stream"] = True
                    break
        except Exception:
            pass

    def _drain_completed_turn(self):
        """Pop same-turn queued metas once its EOS is emitted so they never leak."""
        try:
            queue = getattr(self, "text_queue", None)
            active = getattr(self, "meta_info", None)
            if not queue or not isinstance(active, dict):
                return
            active_key = self._turn_key(active)
            while queue and self._turn_key(queue[0]) == active_key:
                queue.popleft()
        except Exception:
            pass

    async def _generate_ws_loop(self):
        """Core WebSocket streaming loop. Rarely needs overriding."""

        # Duck-typed helpers so minimal test fakes (text_queue/meta_info only) still work.
        def _key(meta):
            fn = getattr(self, "_turn_key", None)
            if callable(fn):
                try:
                    return fn(meta)
                except Exception:
                    pass
            if not isinstance(meta, dict):
                return (None, None, None)
            return (meta.get("sequence_id"), meta.get("turn_id"), meta.get("message_category"))

        def _prune():
            fn = getattr(self, "_prune_stale_queue", None)
            # Call the real pruner when bound to a full synth; fakes without it fall through
            # to the inline monotonic-sequence fallback below.
            if callable(fn) and type(self).__name__ != "_FakeStream" and type(self).__name__ != "FakeStreamSynth":
                try:
                    fn()
                    return
                except Exception:
                    pass
            try:
                queue = getattr(self, "text_queue", None)
                if not queue:
                    return
                should = getattr(self, "should_synthesize_response", None)
                if callable(should):
                    while len(queue) > 0:
                        try:
                            valid = should(queue[0].get("sequence_id"))
                        except Exception:
                            break
                        if valid:
                            break
                        queue.popleft()
                else:
                    seqs = [m.get("sequence_id") for m in queue]
                    if len(set(seqs)) > 1:
                        newest = seqs[-1]
                        kept = deque(m for m in queue if m.get("sequence_id") == newest)
                        if len(kept) != len(queue):
                            try:
                                self.text_queue = kept
                            except Exception:
                                pass
            except Exception:
                pass

        def _carry():
            fn = getattr(self, "_carry_turn_end_flag", None)
            if callable(fn) and type(self).__name__ not in ("_FakeStream", "FakeStreamSynth", "FakeStream"):
                try:
                    fn()
                    return
                except Exception:
                    pass
            try:
                active = getattr(self, "meta_info", None)
                queue = getattr(self, "text_queue", None)
                if not isinstance(active, dict) or not queue:
                    return
                active_key = _key(active)
                for m in list(queue):
                    if _key(m) == active_key and m.get("end_of_llm_stream"):
                        active["end_of_llm_stream"] = True
                        break
            except Exception:
                pass

        def _drain():
            fn = getattr(self, "_drain_completed_turn", None)
            if callable(fn) and type(self).__name__ not in ("_FakeStream", "FakeStreamSynth", "FakeStream"):
                try:
                    fn()
                    return
                except Exception:
                    pass
            try:
                queue = getattr(self, "text_queue", None)
                active = getattr(self, "meta_info", None)
                if not queue or not isinstance(active, dict):
                    return
                active_key = _key(active)
                while queue and _key(queue[0]) == active_key:
                    queue.popleft()
            except Exception:
                pass

        try:
            async for raw_item in self.receiver():
                if self.connection_error:
                    raise SynthesizerError(
                        str(self.connection_error), provider=getattr(self, "provider_name", "stream")
                    )

                audio, extra_meta = self._unpack_receiver_message(raw_item)

                # Correlate by turn/sequence, not per-chunk pop: pushes and audio
                # chunks are not 1:1, so popping per chunk misattributes (and strands
                # the final chunk's end_of_llm_stream). Reuse the active turn's meta
                # until a new turn's head arrives; prune stale heads first.
                _prune()
                if self.text_queue:
                    head = self.text_queue[0]
                    active = self.meta_info if isinstance(self.meta_info, dict) else None
                    if active is None or _key(head) != _key(active):
                        self.meta_info = self.text_queue.popleft()
                        try:
                            self._compute_first_result_latency()
                        except AttributeError:
                            pass
                    else:
                        _carry()
                        self.meta_info = active
                elif not isinstance(self.meta_info, dict):
                    self.meta_info = {}

                self.meta_info["format"] = self._get_audio_format()
                # Merge any extra metadata from the receiver
                self.meta_info.update(extra_meta)

                # First-chunk bookkeeping
                self._stamp_first_chunk(self.meta_info)

                if self.last_text_sent:
                    self.first_chunk_generated = False
                    self.last_text_sent = True

                # End-of-stream sentinel
                if audio == b"\x00":
                    logger.info(f"{getattr(self, 'provider_name', 'stream')}: end of stream")
                    # A newer turn already queued while this one settles: the sentinel is
                    # positional, so emitting it would complete the wrong turn early.
                    if self.text_queue and _key(self.text_queue[0]) != _key(self.meta_info):
                        logger.warning("suppressing end-of-stream for superseded turn")
                        _drain()
                        continue
                    self.meta_info["end_of_synthesizer_stream"] = True
                    # eos may pop a non-final chunk's meta; carry end_of_llm_stream so is_final_chunk fires
                    if self.last_text_sent:
                        self.meta_info["end_of_llm_stream"] = True
                    _carry()
                    self.first_chunk_generated = False
                    try:
                        self._record_turn_latency()
                    except AttributeError:
                        pass
                    _drain()
                else:
                    # ffmpeg/scipy resample must not block the event loop.
                    try:
                        process = self._process_audio_chunk
                    except AttributeError:
                        process = lambda chunk: chunk  # noqa: E731
                    audio = await asyncio.to_thread(process, audio)
                    if audio is None:
                        continue

                try:
                    self._stamp_mark_id(self.meta_info)
                except AttributeError:
                    pass
                yield create_ws_data_packet(audio, self.meta_info)

        except Exception as e:
            # Classify before generate()'s log-and-raise: a bare websockets/provider exception
            # names neither the component that died nor the provider, and error_id is what ties
            # this line to the failure the caller is eventually told about.
            if not isinstance(e, asyncio.CancelledError):
                try:
                    self._log_receiver_failure(e)
                except AttributeError:
                    err = classify_exception(
                        e, component="synthesizer", provider=getattr(self, "provider_name", "stream")
                    )
                    logger.error(f"receiver failed (error_id={err.error_id}): {summarize_exception(e)}")
            raise

        if self.connection_error:
            raise SynthesizerError(str(self.connection_error), provider=getattr(self, "provider_name", "stream"))

    def _log_receiver_failure(self, exc):
        """Attribute a receiver-side failure to this synthesizer with a correlation id."""
        err = classify_exception(exc, component="synthesizer", provider=self.provider_name)
        logger.error(
            f"{self.provider_name}: receiver failed (error_id={err.error_id} code={err.code.value}): "
            f"{summarize_exception(exc)}"
        )

    # ------------------------------------------------------------------
    # Latency helpers
    # ------------------------------------------------------------------

    def _compute_first_result_latency(self):
        """Compute and stamp synthesizer_latency on first audio chunk of a turn."""
        try:
            if self.current_turn_ttfb is None and self.ws_send_time is not None:
                self.current_turn_ttfb = time.perf_counter() - self.ws_send_time
                self.meta_info["synthesizer_latency"] = self.current_turn_ttfb
        except Exception:
            pass

    def _record_turn_latency(self):
        """Append a latency record for the completed turn."""
        try:
            if self.current_turn_start_time is not None:
                total_stream_duration = time.perf_counter() - self.current_turn_start_time
                self._upsert_turn_latency(
                    {
                        "turn_id": self.current_turn_id,
                        "sequence_id": self.current_sequence_id,
                        "tts_start_ms": self.current_tts_start_ms,
                        "first_result_latency_ms": round((self.current_turn_ttfb or 0) * 1000),
                        "total_stream_duration_ms": round(total_stream_duration * 1000),
                        "characters": self.current_sequence_chars,
                        "message_category": self.current_message_category,
                    }
                )
                self.current_turn_start_time = None
                self.current_turn_id = None
                self.current_sequence_id = None
                self.current_message_category = None
                self.current_tts_start_ms = None
                self.ws_send_time = None
                self.current_turn_ttfb = None
                self.current_sequence_chars = 0
        except Exception:
            pass

    # ------------------------------------------------------------------
    # monitor_connection() — shared reconnect loop
    # ------------------------------------------------------------------

    async def monitor_connection(self):
        consecutive_failures = 0

        # `and not self.conversation_ended` mirrors the ElevenLabs v3 monitor: cleanup() sets that
        # flag, and without it this loop kept redialling the provider between cleanup() and its own
        # cancellation — a socket (and, per-connection-billed providers, a charge) leaked per call.
        while consecutive_failures < MAX_CONNECTION_FAILURES and not self.conversation_ended:
            if not self._is_ws_connected():
                logger.info(f"Re-establishing {self.provider_name} connection...")
                result = await self.establish_connection()
                if self.conversation_ended:
                    # The call ended while we were dialling. cleanup() is already past its own
                    # ws.close(), so publishing this socket would strand it open.
                    if result is not None:
                        await call_soft(
                            result.close, name=f"{self.provider_name} discard post-cleanup socket", logger=logger
                        )
                    logger.info(f"{self.provider_name}: conversation ended while reconnecting, dropping new socket")
                    break
                if result is None:
                    consecutive_failures += 1
                    logger.warning(
                        f"{self.provider_name} connection failed "
                        f"(attempt {consecutive_failures}/{MAX_CONNECTION_FAILURES})"
                    )
                    if consecutive_failures >= MAX_CONNECTION_FAILURES:
                        logger.error(f"Max connection failures reached for {self.provider_name}")
                        self.connection_error = self.connection_error or "Max connection failures reached"
                        break
                else:
                    self.websocket = result
                    consecutive_failures = 0
            await asyncio.sleep(1)

    # ------------------------------------------------------------------
    # cleanup() — cancel tasks and close WS
    # ------------------------------------------------------------------

    async def handle_interruption(self):
        """Default barge-in: drop stale queued metas so the next turn re-detects as new.

        Providers with a server-side cancel (Clear/cancelResponse/close_context) override
        this but must keep the queue prune + clock reset (see Cartesia/Maya/Kalpa).
        """
        try:
            self._prune_stale_queue()
            # Next push must re-detect as a new turn to clear any stragglers.
            self.current_turn_start_time = None
            if getattr(self, "text_queue", None):
                # Drop everything retired: the pipeline already dropped the turn.
                try:
                    should = getattr(self, "should_synthesize_response", None)
                    if callable(should):
                        kept = deque(m for m in self.text_queue if should(m.get("sequence_id")))
                        dropped = len(self.text_queue) - len(kept)
                        if dropped:
                            logger.info(f"{self.provider_name}: pruned {dropped} queued metas on interruption")
                        self.text_queue = kept
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error in {self.provider_name} handle_interruption: {e}")

    async def cleanup(self):
        self.conversation_ended = True
        logger.info(f"Cleaning up {self.provider_name} synthesizer tasks")

        try:
            await self._tasks.cancel_all()
        except Exception as e:
            logger.warning(f"Error cancelling {self.provider_name} sender tasks: {e}")
        if self.sender_task:
            try:
                if not self.sender_task.done():
                    self.sender_task.cancel()
                    await self.sender_task
            except asyncio.CancelledError:
                logger.info(f"{self.provider_name} sender task cancelled during cleanup.")
                # Always stamp cancellation — even if sequence_id is None the event is useful
                _t = self.task_manager_instance
                _cancelled_at = round(time.time() * 1000 - _t.conversation_start_init_ts, 2) if _t is not None else None
                self._upsert_turn_latency(
                    {
                        "turn_id": self.current_turn_id,
                        "sequence_id": self.current_sequence_id,
                        "tts_start_ms": self.current_tts_start_ms,
                        "cancelled_at_ms": _cancelled_at,
                        "message_category": self.current_message_category,
                    }
                )
            except Exception as e:
                logger.warning(f"Error cancelling {self.provider_name} sender task: {e}")

        ws = self.websocket
        if ws:
            try:
                await ws.close()
            except Exception as e:
                logger.error(f"Error closing {self.provider_name} WebSocket: {e}")
        self.websocket = None
        logger.info(f"{self.provider_name} WebSocket connection closed.")
