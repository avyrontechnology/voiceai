import asyncio

from voiceai.errors import classify_exception, is_cancellation, summarize_exception
from voiceai.helpers.logger_config import configure_logger
from voiceai.helpers.resilience import call_soft, iteration_guard

logger = configure_logger(__name__)

# Sentinel pushed into _output_queue to unblock generate() on switch
_SWITCH_SENTINEL = object()


class SynthesizerPool:
    """
    Holds multiple pre-warmed synthesizer connections and routes text/audio
    through the active one.

    TTS providers bake voice into the WebSocket at connection time, so we
    maintain one connection per voice. Standby synths keep their connection
    alive via monitor_connection() but receive no text, so no billing occurs.

    Audio output funnels through a single _output_queue. A per-synth
    _run_generate task iterates that synth's generate() and puts results
    into the shared queue. On switch(), the old task is cancelled and a new
    one started; a SENTINEL is pushed so the pool's generate() returns,
    letting __listen_synthesizer's outer while-loop re-enter and pick up
    the new active synth.

    _run_generate RE-ENTERS generate() when it returns. A provider's generate() ends
    (returns, not raises) as soon as its socket closes, and the single-pass version left
    the pool's generate() blocked on an empty _output_queue for the rest of the call — the
    agent went mute even though monitor_connection() had already redialled the socket.
    """

    # generate() returning is normal mid-call (socket closed, provider ended its stream), so the
    # re-entry backoff starts short and grows: a provider that returns instantly in a tight loop
    # must not spin the event loop while monitor_connection() is still dialling.
    _GENERATE_RETRY_INITIAL_S = 0.1
    _GENERATE_RETRY_MAX_S = 2.0
    # Consecutive *raising* passes before the forwarding task gives up. Well past any transient
    # provider fault, so reaching it means this synth cannot produce audio at all.
    _GENERATE_MAX_CONSECUTIVE_FAILURES = 25

    def __init__(self, synthesizers, active_label, multilingual_config):
        """
        Args:
            synthesizers: dict mapping label -> synthesizer instance.
            active_label: which synthesizer should be active initially.
            multilingual_config: raw multilingual config dict from task_config
        """
        self.synthesizers = synthesizers

        if active_label not in self.synthesizers:
            raise ValueError(f"active_label '{active_label}' not in synthesizers: {list(self.synthesizers.keys())}")
        self.active_label = active_label
        self._output_queue = asyncio.Queue()
        self._gen_task = None  # current _run_generate task
        self._monitor_tasks = {}  # label -> monitor task
        self._multilingual_config = multilingual_config
        self._switch_lock = asyncio.Lock()
        # Set by cleanup(): the only thing that stops _run_generate re-entering generate().
        self._stopped = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def connection_time(self):
        return self.synthesizers[self.active_label].connection_time

    @property
    def turn_latencies(self):
        # Per-synth turns are spread across instances after a switch; sort by tts_start_ms for true order.
        all_latencies = []
        for s in self.synthesizers.values():
            all_latencies.extend(s.turn_latencies)
        all_latencies.sort(key=lambda d: d.get("tts_start_ms") if d.get("tts_start_ms") is not None else 0)
        return all_latencies

    @property
    def labels(self):
        return list(self.synthesizers.keys())

    # ------------------------------------------------------------------
    # Delegated methods (forward to active synth)
    # ------------------------------------------------------------------

    async def push(self, message):
        await self.synthesizers[self.active_label].push(message)

    async def handle_interruption(self):
        await self.synthesizers[self.active_label].handle_interruption()

    async def flush_synthesizer_stream(self):
        await self.synthesizers[self.active_label].flush_synthesizer_stream()

    def get_engine(self):
        return self.synthesizers[self.active_label].get_engine()

    def get_sleep_time(self):
        return self.synthesizers[self.active_label].get_sleep_time()

    def supports_websocket(self):
        return self.synthesizers[self.active_label].supports_websocket()

    def get_synthesized_characters(self):
        total = 0
        for s in self.synthesizers.values():
            total += s.get_synthesized_characters()
        return total

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def monitor_connection(self):
        """Start monitor_connection() on ALL synthesizers (keeps standby WebSockets alive)."""
        for label, synth in self.synthesizers.items():
            task = asyncio.create_task(synth.monitor_connection())
            self._monitor_tasks[label] = task
            logger.info(f"SynthesizerPool: monitor started for '{label}'")

        # Start the generate-forwarding task for the active synth
        self._gen_task = asyncio.create_task(self._run_generate(self.active_label))
        logger.info(f"SynthesizerPool: generate task started for active='{self.active_label}'")

    async def _run_generate(self, label):
        """Forward synth.generate() into the shared _output_queue, re-entering it when it ends.

        generate() RETURNS (it does not raise) once the provider socket closes, so a single pass
        left generate() below parked on an empty queue forever and the agent mute for the rest of
        the call. monitor_connection() redials in the background, so re-entering picks the new
        socket up. Only cleanup() (via _stopped), the provider ending the conversation, or
        cancellation stops this task; a raising pass is isolated by the guard instead of ending it.
        """
        synth = self.synthesizers[label]
        guard = iteration_guard(
            f"synthesizer_pool.generate[{label}]",
            logger=logger,
            max_consecutive=self._GENERATE_MAX_CONSECUTIVE_FAILURES,
            backoff_initial=self._GENERATE_RETRY_INITIAL_S,
            backoff_max=self._GENERATE_RETRY_MAX_S,
        )
        backoff = self._GENERATE_RETRY_INITIAL_S
        passes = 0
        try:
            while not self._should_stop_generating(synth):
                forwarded = None
                async with guard:
                    forwarded = await self._forward_generate(synth, label)
                passes += 1
                if self._should_stop_generating(synth):
                    break
                if forwarded is None:
                    # The pass raised: the guard already logged it and served its own backoff,
                    # so re-enter straight away instead of compounding two waits.
                    continue
                if forwarded:
                    # The pass did produce audio, so this is a fresh drop rather than a
                    # provider that keeps returning immediately: start the backoff over.
                    backoff = self._GENERATE_RETRY_INITIAL_S
                logger.warning(
                    f"SynthesizerPool: generate() for '{label}' ended after {forwarded} packet(s) "
                    f"(pass {passes}) — re-entering in {backoff:.2f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._GENERATE_RETRY_MAX_S)
        except asyncio.CancelledError:
            logger.info(f"SynthesizerPool: _run_generate cancelled for '{label}'")
        except Exception as e:
            # LoopFailure from the guard: every pass raised. Nothing left to retry here — the
            # turn watchdogs in the task manager are what surface the silence to the caller.
            logger.error(f"SynthesizerPool: _run_generate for '{label}' gave up: {summarize_exception(e)}")
        else:
            logger.info(f"SynthesizerPool: _run_generate finished for '{label}' after {passes} pass(es)")

    def _should_stop_generating(self, synth):
        """True once cleanup() ran or the synth itself declared the conversation over."""
        return self._stopped or bool(getattr(synth, "conversation_ended", False))

    async def _forward_generate(self, synth, label):
        """One pass over synth.generate(); returns how many packets it forwarded."""
        forwarded = 0
        try:
            async for message in synth.generate():
                self._output_queue.put_nowait(message)
                forwarded += 1
        except Exception as e:
            if is_cancellation(e):
                raise
            # Classify and log here rather than leaving the guard to report a bare provider
            # exception: error_id is what ties this line to the call report, and the code says
            # whether the failure was a dropped connection or something the retry cannot fix.
            err = classify_exception(e, component="synthesizer", provider=getattr(synth, "provider_name", label))
            logger.error(
                f"SynthesizerPool: generate() for '{label}' failed after {forwarded} packet(s) "
                f"(error_id={err.error_id} code={err.code.value}): {summarize_exception(e)}"
            )
            raise err from e
        return forwarded

    async def generate(self):
        """Async generator that yields audio packets from the active synthesizer.

        Returns (stops iteration) when a _SWITCH_SENTINEL is encountered,
        which signals __listen_synthesizer to re-enter via the outer while loop.
        """
        while True:
            message = await self._output_queue.get()
            if message is _SWITCH_SENTINEL:
                logger.info("SynthesizerPool: generate() received SWITCH_SENTINEL, returning")
                return
            yield message

    # ------------------------------------------------------------------
    # Switching
    # ------------------------------------------------------------------

    async def switch(self, label):
        """Switch the active synthesizer.

        1. Cancel the old _run_generate task (forcefully breaks any blocked recv()).
        2. Set the new active label.
        3. Start a new _run_generate task for the new synth.
        4. Push SENTINEL so pool.generate() returns → __listen_synthesizer re-enters.

        Serialized by switch_lock: the await on the old task's cancellation is a
        suspension point, so without the lock two concurrent switches could both
        start a _run_generate for the same label and double-recv() the websocket.
        """
        # Serialize: concurrent switches would each start a _run_generate (dual recv on one ws).
        async with self._switch_lock:
            if label == self.active_label:
                logger.info(f"SynthesizerPool: already active on '{label}', no-op")
                return

            if label not in self.synthesizers:
                raise ValueError(f"Unknown synthesizer label '{label}'. Available: {list(self.synthesizers.keys())}")

            old = self.active_label

            if self._gen_task and not self._gen_task.done():
                self._gen_task.cancel()
                try:
                    await self._gen_task
                except asyncio.CancelledError:
                    pass
                logger.info(f"SynthesizerPool: cancelled generate task for '{old}'")

            # Quiesce the outgoing synth before the new one starts producing. Cancelling the
            # forwarding task stops us READING it, but the provider may still be mid-turn and
            # would keep buffering audio for a language nobody is speaking any more; the audio
            # also outlives the switch on a socket that is kept warm as a standby. Best-effort:
            # a provider that cannot cancel a turn must not block the switch.
            old_synth = self.synthesizers[old]
            if hasattr(old_synth, "handle_interruption"):
                await call_soft(
                    old_synth.handle_interruption,
                    name=f"SynthesizerPool: handle_interruption on '{old}'",
                    logger=logger,
                )

            self.active_label = label
            self._gen_task = asyncio.create_task(self._run_generate(label))
            logger.info(f"SynthesizerPool: started generate task for '{label}'")

            self._output_queue.put_nowait(_SWITCH_SENTINEL)
            logger.info(f"SynthesizerPool: switched {old} -> {label}")

    # ------------------------------------------------------------------
    # Active synth info
    # ------------------------------------------------------------------

    def get_active_synthesizer_info(self):
        """Return metadata about the active synthesizer (e.g. provider, voice)."""
        active_synth = self._multilingual_config.get(self.active_label, {})
        info = {
            "provider": active_synth.get("provider"),
            "voice": active_synth.get("provider_config", {}).get("voice"),
        }

        return info

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self):
        """Clean up all synthesizers and cancel all tasks."""
        # Before cancelling: _run_generate re-enters generate() on its own, so without this a
        # pass that is mid-flight here would simply dial the provider again.
        self._stopped = True

        # Cancel generate task
        if self._gen_task and not self._gen_task.done():
            self._gen_task.cancel()
            try:
                await self._gen_task
            except asyncio.CancelledError:
                pass

        # Cancel monitor tasks
        for label, task in self._monitor_tasks.items():
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            logger.info(f"SynthesizerPool: monitor cancelled for '{label}'")

        # Cleanup each synthesizer
        for label, synth in self.synthesizers.items():
            logger.info(f"SynthesizerPool: cleaning up '{label}'")
            await synth.cleanup()

        logger.info("SynthesizerPool: cleanup complete")
