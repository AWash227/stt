"""
Audio-chunk aggregator with rolling tail-overlap.

• Collects VAD slices and groups them into sentence-sized segments.
• Prepends the last 0.5 s of the *previous* segment to the next one,
  giving the ASR extra acoustic context for smoother word-boundaries.
"""

import queue
import threading
import numpy as np
import time
import logging
import config

log = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────
TAIL_SEC = 0.5  # seconds of audio to overlap between segments
TAIL_SAMPLES = int(TAIL_SEC * config.Audio.SAMPLE_RATE)
# ────────────────────────────────────────────────────────────


def worker(
    input_q: "queue.Queue[np.ndarray]",
    output_q: "queue.Queue[np.ndarray]",
    shutdown_event: threading.Event,
):
    log.info(
        f"Aggregator started (flush={config.Aggregator.FLUSH_TIMEOUT_S}s, "
        f"tail={TAIL_SEC}s)"
    )

    buffer: list[np.ndarray] = []
    last_chunk_time: float | None = None
    max_buffer_samples = int(config.Audio.SAMPLE_RATE * config.Aggregator.MAX_BUFFER_S)
    prev_tail: np.ndarray = np.empty(0, dtype=np.float32)

    # ────────────────────────────────────────────────────────
    def flush_buffer():
        nonlocal buffer, prev_tail
        if not buffer:
            return
        try:
            segment = np.concatenate(buffer)
        except Exception as e:
            log.error(f"Aggregator concat error: {e}")
            buffer = []
            return

        # prepend tail from previous segment
        segment_with_tail = (
            np.concatenate([prev_tail, segment]) if prev_tail.size else segment
        )

        log.info(
            f"→ Aggregated {len(buffer)} chunks "
            f"({len(segment) / config.Audio.SAMPLE_RATE:.2f}s) "
            f"(+{len(prev_tail)/config.Audio.SAMPLE_RATE:.2f}s tail)"
        )
        output_q.put(segment_with_tail)

        # save new tail for next time
        prev_tail = segment[-TAIL_SAMPLES:]
        buffer = []

    # ────────────────────────────────────────────────────────
    while not shutdown_event.is_set():
        try:
            chunk = input_q.get(timeout=0.1)
            buffer.append(chunk)
            last_chunk_time = time.monotonic()

            if sum(len(c) for c in buffer) >= max_buffer_samples:
                log.warning("Aggregator: max buffer reached; flushing.")
                flush_buffer()
                last_chunk_time = None

        except queue.Empty:
            if (
                last_chunk_time
                and (time.monotonic() - last_chunk_time)
                > config.Aggregator.FLUSH_TIMEOUT_S
            ):
                flush_buffer()
                last_chunk_time = None

    # drain remaining audio on shutdown
    flush_buffer()
    log.info("Aggregator exited cleanly.")
