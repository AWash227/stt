"""
Incremental ASR with word-diff de-duplication
─────────────────────────────────────────────
This is the same pattern used by:
• Whisper.cpp  (`examples/stream`)   :contentReference[oaicite:0]{index=0}
• OpenAI Whisper live caption demo   :contentReference[oaicite:1]{index=1}
• NeMo Buffered-Streaming notebook   :contentReference[oaicite:2]{index=2}

Workflow
========
1.  Mic/VAD → Aggregator emits CHUNK_SEC (≈2 s) *PLUS* LOOK_BACK_SEC
    (≈0.5 s) of overlap for acoustic context.
2.  We keep the running `prev_text`.  After we get `new_text`
    from NeMo we do a *word-diff* to find the longest suffix of
    `prev_text` that is also a prefix of `new_text`.
3.  Only the *appendix* (new words) is pushed to `text_q`.

No timestamps, no guessing inter-word timing, zero duplication.
"""

from __future__ import annotations
import queue, threading, logging
from typing import List

import numpy as np
import torch
import time
import json
import difflib
import config

log = logging.getLogger(__name__)

try:
    import nemo.collections.asr as nemo_asr

    logging.getLogger("nemo_toolkit").setLevel(logging.ERROR)
except ImportError:  #  graceful degradation
    nemo_asr = None


# ────────────────────────────
#  Model loading
# ────────────────────────────
def load_model():
    if nemo_asr is None:
        log.error("[asr] NeMo toolkit is not installed.")
        return None

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"[asr] Loading model on {device}…")
    try:
        model = nemo_asr.models.ASRModel.restore_from(
            config.ASR.MODEL_PATH,
            map_location=torch.device(device),
        ).eval()
        return model
    except Exception as e:
        log.error(f"[asr] Failed to load model: {e}")
        return None


def transcribe_chunk(model, audio: np.ndarray) -> str:
    if model is None or audio.size == 0:
        return ""
    try:
        hyp = model.transcribe([audio], batch_size=1, return_hypotheses=False)
        return hyp[0].text.strip() if isinstance(hyp, list) else str(hyp).strip()
    except Exception as e:
        log.error(f"[asr] Transcription error: {e}")
        return ""


# ────────────────────────────
#  Word-diff helper
# ────────────────────────────
def diff_appendix(prev: str, new: str) -> str:
    """
    Return the part of *new* that doesn't overlap with the tail of *prev*.

    We tokenise on whitespace, then walk the largest common suffix/prefix.
    """
    if not prev:
        return new

    prev_words = prev.split()
    new_words = new.split()
    max_k = min(len(prev_words), len(new_words))

    # walk from longest to shortest suffix
    for k in range(max_k, 0, -1):
        if prev_words[-k:] == new_words[:k]:
            return " ".join(new_words[k:])
    return new


# ────────────────────────────
#  Worker thread
# ────────────────────────────
MIN_WORDS = 2


def worker(
    model,
    chunk_q: "queue.Queue[np.ndarray]",
    text_q: queue.Queue,
    pipeline_event_q: queue.Queue,
    shutdown_event: threading.Event,
):
    log.info("ASR worker (word-diff) started.")

    prev_text = ""

    while not shutdown_event.is_set():
        try:
            audio_np = chunk_q.get(timeout=0.5)
        except queue.Empty:
            continue

        pipeline_event_q.put("asr_start")
        new_text = transcribe_chunk(model, audio_np)
        if not new_text:
            continue

        appendix = diff_appendix(prev_text, new_text).strip()
        if len(appendix.split()) < MIN_WORDS:
            continue

        prev_text = f"{prev_text} {appendix}".strip()
        log.info(f"[asr] >>> {appendix}")
        text_q.put(appendix)

    log.info("ASR worker stopped.")
