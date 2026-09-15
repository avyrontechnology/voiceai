import time
import asyncio
import threading
import queue
import json
from dotenv import load_dotenv

from google.cloud import speech_v1p1beta1 as speech

from .base_transcriber import BaseTranscriber
from voiceai.enums import TelephonyProvider
from voiceai.otobaai_logger import get_logger
from voiceai.helpers.utils import create_ws_data_packet, timestamp_ms

load_dotenv()
logger = get_logger(__name__)


class GoogleTranscriber(BaseTranscriber):
    """
    Streaming transcriber using Google Cloud Speech-to-Text.
    Uses threading to bridge async input_queue with blocking gRPC streaming.
    """

    def __init__(
        self,
        telephony_provider,
        input_queue=None,
        output_queue=None,
        language="en-US",
        encoding=None,
        sample_rate_hertz=None,
        model="latest_long",
        run_id="",
        **kwargs,
    ):
        super().__init__(input_queue)
        self.provider = telephony_provider or ""
        self.transcriber_output_queue = output_queue  # expected to be asyncio.Queue in TaskManager
        self.language = language
        self.model = model
        self.run_id = run_id or kwargs.get("run_id", "")

        # Provider-specific audio configuration
        if self.provider in TelephonyProvider.telephony_values():
            self.encoding = "MULAW" if self.provider in TelephonyProvider.mulaw_values() else "LINEAR16"
            self.sample_rate_hertz = 8000
        elif self.provider == "web_based_call":
            self.encoding = "LINEAR16"
            self.sample_rate_hertz = 16000
        elif self.provider == "playground":
            self.encoding = "LINEAR16"
            self.sample_rate_hertz = 8000
        else:
            self.encoding = encoding or "LINEAR16"
            self.sample_rate_hertz = sample_rate_hertz or 16000

        # Google client using Application Default Credentials
        self.client = speech.SpeechClient()

        # Threading bridge for gRPC streaming
        self._audio_q = queue.Queue()
        self._running = False
        self._grpc_thread = None
        # Session rotation (Live-API-cap style): set on EOS so the transcribe loop
        # knows a stream end is teardown, not a cap to rotate over.
        self._eos_received = False
        # Last interim text for _flush_pending_final on rotation (mid-turn cap).
        self._last_interim = ""

        # Connection state management
        self.connection_start_time = None
        self.connection_time = None
        self.websocket_connection = None
        self.connection_authenticated = False
        self.transcription_task = None

        # Audio frame tracking
        self.audio_frame_duration = 0.0
        self.num_frames = 0
        if self.provider in TelephonyProvider.telephony_values():
            self.audio_frame_duration = 0.2
        elif self.provider == "web_based_call":
            self.audio_frame_duration = 0.256
        elif self.provider == "playground":
            self.audio_frame_duration = 0.0

        # Turn latency tracking
        self.turn_latencies = []
        self.current_turn_start_time = None
        self.current_turn_id = None
        self.turn_counter = 0
        self._turn_start_epoch_ms = None

        # Request tracking
        self.meta_info = None
        self._request_id = None
        self.audio_submitted = False
        self.audio_submission_time = None

        # Event loop reference for thread-safe queue operations
        try:
            self.loop = asyncio.get_event_loop()
        except Exception:
            self.loop = None

    def _enqueue_output(self, data, meta=None):
        """Thread-safe enqueue to transcriber_output_queue."""
        if self.transcriber_output_queue is None:
            return

        packet = create_ws_data_packet(data, meta or self.meta_info or {})

        # Thread-safe asyncio queue operation
        try:
            if self.loop and isinstance(self.transcriber_output_queue, asyncio.Queue):
                future = asyncio.run_coroutine_threadsafe(self.transcriber_output_queue.put(packet), self.loop)
                try:
                    future.result(timeout=2.0)
                except Exception:
                    pass
                return
        except Exception:
            pass

        # Fallback to sync operations
        try:
            if hasattr(self.transcriber_output_queue, "put_nowait"):
                self.transcriber_output_queue.put_nowait(packet)
            elif hasattr(self.transcriber_output_queue, "put"):
                self.transcriber_output_queue.put(packet)
        except Exception:
            logger.exception("Failed to enqueue packet to transcriber_output_queue")

    async def google_connect(self):
        """Validate Google Speech client connection."""
        try:
            start_time = time.perf_counter()
            _ = self.client
            self.connection_authenticated = True

            if not self.connection_time:
                self.connection_time = round((time.perf_counter() - start_time) * 1000)

            logger.info("Successfully validated Google Speech client")
            return True

        except Exception as e:
            logger.error(f"Failed to validate Google Speech client: {e}")
            raise ConnectionError(f"Failed to validate Google Speech client: {e}")

    async def run(self):
        """
        Enhanced startup sequence matching Deepgram pattern.
        """
        try:
            # Connection validation
            await self.google_connect()

            self._running = True

            # Create transcription task like Deepgram (rotation-aware transcribe loop).
            self.transcription_task = asyncio.create_task(self.transcribe())

        except Exception as e:
            logger.exception(f"Error starting GoogleTranscriber: {e}")
            self.connection_error = str(e)
            await self.toggle_connection()
            meta = (self.meta_info or {}).copy()
            meta["connection_error"] = self.connection_error
            await self.transcriber_output_queue.put(create_ws_data_packet("transcriber_connection_closed", meta))

    async def transcribe(self):
        """Stream until eos/shutdown, rotating the gRPC session while connection_on.

        Mirrors GeminiTranscriber.transcribe: the sender runs once outside the loop
        feeding _audio_q, each loop iteration runs one blocking streaming_recognize
        session in a thread, and a server-side stream end (cap/transient) flushes the
        pending turn and rotates instead of ending the call.
        """
        sender_task = None
        try:
            sender_task = asyncio.create_task(self._send_audio_to_transcriber())
            while self.connection_on and not self._eos_received:
                loop = asyncio.get_event_loop()
                try:
                    await loop.run_in_executor(None, self._run_grpc_session_once)
                except Exception as e:
                    logger.error(f"Error in Google gRPC session: {e}")
                    self.connection_error = str(e)
                # Server closed the stream (cap/transient) but the call is alive:
                # flush the mid-turn partial so no words are lost, then rotate.
                reconnect = self.connection_on and not self._eos_received and not self.connection_error
                # A session error without connection_on cleared is also a transient to
                # rotate over (cap/unavailable), unless EOS already arrived.
                if self.connection_error and self.connection_on and not self._eos_received:
                    # Classify for observability (task_manager turns connection_error
                    # into TranscriberError); keep raw string on connection_error.
                    try:
                        from voiceai.transcriber.exceptions import classify_exception as _classify

                        _classified = _classify(
                            Exception(self.connection_error),
                            component="transcriber",
                            provider="google",
                            model=self.model,
                        )
                        logger.warning(f"Google transient classified as {_classified.code}: {self.connection_error}")
                    except Exception:
                        pass
                    self.connection_error = None
                    reconnect = True
                if reconnect:
                    flushed = self._flush_pending_final()
                    if flushed is not None:
                        self._enqueue_output(flushed["data"], meta=flushed["meta"])
                    logger.info("Rotating Google Speech session (cap reached or transient drop)")
                    continue
                break
        except Exception as e:
            logger.error(f"Error in Google transcribe loop: {e}")
            await self.toggle_connection()
        finally:
            if sender_task is not None and not sender_task.done():
                sender_task.cancel()
            if hasattr(self, "transcription_task") and self.transcription_task:
                try:
                    current = asyncio.current_task()
                    if self.transcription_task is not current:
                        self.transcription_task.cancel()
                except Exception:
                    pass
            # Push terminal sentinel like the other providers.
            try:
                meta = dict(self.meta_info or {})
                if self.connection_error:
                    meta["connection_error"] = self.connection_error
                self._enqueue_output("transcriber_connection_closed", meta=meta)
            except Exception:
                pass

    async def _transcribe_wrapper(self):
        """Legacy entry: delegate to the rotation-aware transcribe loop."""
        await self.transcribe()

    async def _send_audio_to_transcriber(self):
        """Reads packets from input_queue and forwards to gRPC thread via _audio_q."""
        try:
            while True:
                ws_data_packet = await self.input_queue.get()

                # Initialize metadata on first audio packet
                if not self.audio_submitted:
                    self.audio_submitted = True
                    self.audio_submission_time = time.time()
                    self._request_id = self.generate_request_id()
                    self.meta_info = ws_data_packet.get("meta_info", {}) or {}
                    self.meta_info["request_id"] = self._request_id
                    try:
                        self.meta_info["transcriber_start_time"] = time.perf_counter()
                        # start turn-level tracking
                        self.current_turn_start_time = self.meta_info["transcriber_start_time"]
                        self._turn_start_epoch_ms = timestamp_ms()
                        self.turn_counter += 1
                        self.current_turn_id = self.turn_counter
                    except Exception:
                        pass

                # check EOS
                if ws_data_packet.get("meta_info", {}).get("eos") is True:
                    self._eos_received = True
                    # put sentinel so blocking generator ends gracefully
                    self._audio_q.put(None)
                    break

                # get raw audio bytes from packet and track frames
                data = ws_data_packet.get("data")
                if data:
                    self.num_frames += 1
                    # if data is base64 string, try to decode if needed
                    if isinstance(data, str):
                        try:
                            # if base64 encoded, decode (guardy)
                            import base64 as _b64

                            d = _b64.b64decode(data)
                            self._audio_q.put(d)
                        except Exception:
                            # fallback: push raw str bytes
                            self._audio_q.put(data.encode("utf-8"))
                    else:
                        # assume bytes-like
                        self._audio_q.put(data)
        except Exception:
            logger.exception("Error in _send_audio_to_transcriber")

    def _audio_generator(self):
        """
        Blocking generator consumed by google client.streaming_recognize.
        Yields StreamingRecognizeRequest(audio_content=...).
        """
        while True:
            chunk = self._audio_q.get()
            if chunk is None:
                # sentinel: end of stream
                return
            # ensure bytes
            if isinstance(chunk, bytes):
                yield speech.StreamingRecognizeRequest(audio_content=chunk)
            else:
                # try to coerce
                try:
                    yield speech.StreamingRecognizeRequest(audio_content=bytes(chunk))
                except Exception:
                    logger.exception("Non-bytes chunk received in google audio generator; dropping")

    def _append_turn_latency(self, final_transcript=None):
        """
        Add a turn latency entry compatible with the Deepgram 'turn_latencies' entries.
        Called when a final transcript arrives (end-of-turn semantics).
        """
        try:
            if self.current_turn_id and self.current_turn_start_time:
                first_ms = int(round((self.meta_info.get("transcriber_first_result_latency", 0)) * 1000))
                total_s = (time.perf_counter() - self.current_turn_start_time) if self.current_turn_start_time else 0
                entry = {
                    "turn_id": self.current_turn_id,
                    "sequence_id": self.current_turn_id,
                    "first_result_latency_ms": first_ms,
                    "total_stream_duration_ms": int(round(total_s * 1000)),
                    "asr_start_epoch_ms": self._turn_start_epoch_ms,
                    "asr_finalized_epoch_ms": timestamp_ms(),
                }
                if final_transcript:
                    entry["final_transcript"] = final_transcript
                self._upsert_turn_latency(entry)
                # also expose on meta_info for immediate consumption (copy, not alias)
                try:
                    import copy as _copy

                    self.meta_info["turn_latencies"] = _copy.deepcopy(self.turn_latencies)
                except Exception:
                    pass
                # reset turn tracking
                self.current_turn_start_time = None
                self._turn_start_epoch_ms = None
                self.current_turn_id = None
        except Exception:
            logger.exception("Error appending turn latency")

    def _flush_pending_final(self):
        """On session rotation mid-turn, don't lose the partial: publish it as the turn.

        Mirrors GeminiTranscriber._flush_pending_final. Returns {"data","meta"} or None.
        """
        text = (self._last_interim or "").strip()
        if text and self.current_turn_id is not None and not self.is_transcript_sent_for_processing:
            try:
                self._append_turn_latency(text)
            except Exception:
                pass
            data = {"type": "transcript", "content": text, "force_finalized": True}
            meta = dict(self.meta_info or {})
            # Mark claimed so a late final for the rotated session cannot duplicate.
            self.is_transcript_sent_for_processing = True
            self._last_interim = ""
            return {"data": data, "meta": meta}
        return None

    def _run_grpc_stream(self):
        """Legacy single-entry wrapper: one session (rotation lives in transcribe())."""
        return self._run_grpc_session_once()

    def _run_grpc_session_once(self):
        """
        One blocking streaming_recognize session (no terminal sentinel).
        The transcribe() loop decides rotate-vs-teardown and emits the sentinel.
        Blocking thread target that runs google streaming_recognize and iterates responses.
        Pushes interim and final transcripts back onto transcriber_output_queue (thread-safely).
        """
        try:
            # Connection establishment timing
            if not self.connection_start_time:
                self.connection_start_time = time.time()
            # build recognition config
            # map string encodings to enum (google cloud)
            encoding_enum = speech.RecognitionConfig.AudioEncoding.LINEAR16
            enc = (self.encoding or "").upper()
            if "MULAW" in enc or "ULAW" in enc:
                encoding_enum = speech.RecognitionConfig.AudioEncoding.MULAW
            elif "LINEAR" in enc or "PCM" in enc:
                encoding_enum = speech.RecognitionConfig.AudioEncoding.LINEAR16
            else:
                # fallback: leave as unspecified (google will attempt sampling inference)
                try:
                    encoding_enum = speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED
                except Exception:
                    encoding_enum = speech.RecognitionConfig.AudioEncoding.LINEAR16

            recognition_config = speech.RecognitionConfig(
                encoding=encoding_enum,
                sample_rate_hertz=int(self.sample_rate_hertz),
                language_code=self.language,
                model=self.model,
                enable_automatic_punctuation=True,
                max_alternatives=1,
            )

            streaming_config = speech.StreamingRecognitionConfig(
                config=recognition_config,
                interim_results=True,
                single_utterance=False,
            )

            requests = self._audio_generator()

            try:
                responses = self.client.streaming_recognize(streaming_config, requests)
                self.connection_authenticated = True

                # iterate responses synchronously
                for response in responses:
                    if not self._running or not self.connection_on:
                        break
                    if not response.results:
                        continue

                    result = response.results[0]
                    is_final = result.is_final
                    transcript = ""
                    if result.alternatives:
                        transcript = result.alternatives[0].transcript.strip()

                    if transcript:
                        # set first-result latency if not already set
                        try:
                            if (
                                self.meta_info
                                and "transcriber_start_time" in self.meta_info
                                and "transcriber_first_result_latency" not in self.meta_info
                            ):
                                self.meta_info["transcriber_first_result_latency"] = (
                                    time.perf_counter() - self.meta_info["transcriber_start_time"]
                                )
                        except Exception:
                            pass

                        # Prepare packet
                        if is_final:
                            # populate total durations and append turn latencies
                            try:
                                if self.meta_info and "transcriber_start_time" in self.meta_info:
                                    self.meta_info["transcriber_total_stream_duration"] = (
                                        time.perf_counter() - self.meta_info["transcriber_start_time"]
                                    )
                            except Exception:
                                pass

                            # append to turn_latencies (A)
                            self._append_turn_latency(transcript)
                            self._last_interim = ""

                            data = {"type": "transcript", "content": transcript}
                            self._enqueue_output(data, meta=self.meta_info)
                        else:
                            self._last_interim = transcript
                            data = {"type": "interim_transcript_received", "content": transcript}
                            self._enqueue_output(data, meta=self.meta_info)

                # No terminal sentinel here: transcribe() owns rotate-vs-teardown so a
                # mid-call cap rotates instead of ending the call.

            except Exception as stream_error:
                # Specific gRPC error handling — no terminal sentinel here: transcribe()
                # owns rotate-vs-teardown (a cap must rotate, not end the call).
                error_msg = f"Google streaming error: {stream_error}"
                logger.error(error_msg)

                # Determine if error is retryable
                if "deadline exceeded" in str(stream_error).lower():
                    logger.info("Deadline exceeded - this may be normal for long streams")
                elif "unavailable" in str(stream_error).lower():
                    logger.warning("Service unavailable - connection issue")

                self.connection_error = str(stream_error)
                return

        except Exception as e:
            # Configuration or setup error — transcribe() emits the single sentinel.
            logger.exception(f"Google transcriber setup error: {e}")
            self.connection_error = str(e)
        finally:
            self.connection_authenticated = False

    async def toggle_connection(self):
        """
        Called by TaskManager to force-close connection.
        Enhanced cleanup matching Deepgram pattern.
        """
        logger.info("toggle_connection called on GoogleTranscriber")
        self._running = False
        self.connection_on = False
        self.connection_authenticated = False

        # Cancel transcription task if running
        if hasattr(self, "transcription_task") and self.transcription_task:
            try:
                self.transcription_task.cancel()
            except Exception:
                pass

        # Signal thread to stop
        try:
            self._audio_q.put(None)
        except Exception:
            pass

        # Wait for thread cleanup with timeout — join() blocks up to 2s, so it must
        # run off the event loop or every other call on this worker stalls.
        if hasattr(self, "_grpc_thread") and self._grpc_thread and self._grpc_thread.is_alive():
            try:
                await asyncio.to_thread(self._grpc_thread.join, 2.0)
                if self._grpc_thread.is_alive():
                    logger.warning("gRPC thread did not terminate within timeout")
            except Exception as e:
                logger.error(f"Error joining gRPC thread: {e}")

        logger.info("GoogleTranscriber connection toggled off")

    def _sync_cleanup(self):
        """
        Synchronous cleanup of Google resources.
        """
        try:
            self._running = False
            self.connection_on = False
            self.connection_authenticated = False

            # Signal thread to stop
            self._audio_q.put(None)

            # Wait for thread with timeout
            if self._grpc_thread and self._grpc_thread.is_alive():
                self._grpc_thread.join(timeout=2.0)
                if self._grpc_thread.is_alive():
                    logger.warning("gRPC thread did not terminate gracefully")

            self._grpc_thread = None

            # Reset connection state
            self.connection_start_time = None
            self.connection_time = None

            logger.info("GoogleTranscriber sync cleanup completed")

        except Exception:
            logger.exception("cleanup error in GoogleTranscriber")

    async def cleanup(self):
        """Clean up all resources including gRPC thread and tasks."""
        logger.info("Cleaning up Google transcriber resources")

        # Cancel transcription task properly
        if (
            hasattr(self, "transcription_task")
            and self.transcription_task is not None
            and not self.transcription_task.done()
        ):
            self.transcription_task.cancel()
            try:
                await self.transcription_task
            except asyncio.CancelledError:
                logger.info("Google transcription_task cancelled")
            except Exception as e:
                logger.warning(f"Error cancelling Google transcription_task: {e}")

        # Run sync cleanup in executor to not block event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_cleanup)

    def get_meta_info(self):
        return self.meta_info
