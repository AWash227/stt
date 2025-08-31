#!/usr/bin/env python3
import webrtcvad
import numpy as np
import queue

MAX_AUDIO_BUFFER_SEC = 30
SAMPLE_RATE = 16000

MIN_UTTERANCE_LEN = int(SAMPLE_RATE * 0.2)  # 0.2 seconds
VAD_SENSITIVITY = 2  # Most aggressive
VAD_CHUNK_MS = 30  # 30 ms window
MAX_SILENCE_MS = 600  # Flush after 0.6s silence

vad = webrtcvad.Vad(VAD_SENSITIVITY)


def worker(
    audio_q: "queue.Queue[np.ndarray]",
    chunk_queue: "queue.Queue[np.ndarray]",
    shutdown_event,
    dict_control,
    vad_event_q: "queue.Queue[None]" = None,
):
    """
    Segments audio from audio_q via WebRTC VAD,
    pushes completed utterances to chunk_queue,
    and emits a flash event into vad_event_q on each flush.
    """
    max_silence_chunks = MAX_SILENCE_MS // VAD_CHUNK_MS
    max_audio_buffer_len = SAMPLE_RATE * MAX_AUDIO_BUFFER_SEC

    audio_buffer = []
    silence_chunks = 0

    while not shutdown_event.is_set():
        try:
            audio = audio_q.get(timeout=0.5)
        except queue.Empty:
            continue

        arr = np.array(audio, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.mean(axis=1)
        arr = arr.flatten()
        # convert to int16 for webrtcvad
        int16 = (arr * 32767).astype(np.int16)

        # one VAD window
        win_size = int(VAD_CHUNK_MS / 1000 * SAMPLE_RATE)
        win_bytes = int16[:win_size].tobytes()
        try:
            is_speech = vad.is_speech(win_bytes, sample_rate=SAMPLE_RATE)
        except Exception as e:
            print(f"[vad] VAD error: {e}")
            is_speech = False

        if is_speech:
            audio_buffer.append(int16)
            silence_chunks = 0
            # safety flush if too long
            total_len = sum(len(c) for c in audio_buffer)
            if total_len > max_audio_buffer_len:
                _flush(audio_buffer, chunk_queue, dict_control, vad_event_q)
                audio_buffer.clear()
                silence_chunks = 0

        elif audio_buffer:
            silence_chunks += 1
            if silence_chunks >= max_silence_chunks:
                _flush(audio_buffer, chunk_queue, dict_control, vad_event_q)
                audio_buffer.clear()
                silence_chunks = 0


def _flush(
    audio_buffer: list[np.ndarray],
    chunk_queue: "queue.Queue[np.ndarray]",
    dict_control,
    vad_event_q: "queue.Queue[None]" = None,
):
    """
    Called when an utterance ends:
      • notify display via vad_event_q,
      • if dictation active, enqueue concatenated chunk.
    """
    # signal the UI to flash
    if vad_event_q is not None:
        vad_event_q.put(None)

    if not dict_control.is_active():
        print("[vad] Dictation not active, dropping chunk.")
        return

    try:
        full = np.concatenate(audio_buffer)
    except Exception as e:
        print(f"[vad] Buffer concat error: {e}")
        return

    chunk_queue.put(full)
    print(f"[vad] Flushed {len(full)/SAMPLE_RATE:.2f}s audio to chunk_queue.")
