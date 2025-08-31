# src/vad.py
import queue
import threading
import numpy as np
import torch
import logging
import soundfile as sf
from enum import Enum
from collections import deque
import config

log = logging.getLogger(__name__)

try:
    from nemo.collections.asr.models import EncDecFrameClassificationModel

    logging.getLogger("nemo_toolkit").setLevel(logging.ERROR)
except ImportError:
    EncDecFrameClassificationModel = None


class VadState(Enum):
    SILENCE = 1
    SPEECH = 2


class SharedVadState:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = "SILENCE"

    def set(self, state):
        with self._lock:
            self._state = state

    def get(self):
        with self._lock:
            return self._state


shared_vad_state = SharedVadState()


def load_model():
    if EncDecFrameClassificationModel is None:
        log.error("NeMo not installed. VAD cannot function.")
        return None
    try:
        log.info("Loading VAD model...")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = EncDecFrameClassificationModel.from_pretrained(
            model_name=config.VAD.MODEL_NAME
        )
        model.to(device)
        model.eval()
        log.info(f"VAD model loaded on device: {device}")
        return model
    except Exception as e:
        log.error(f"Error loading VAD model: {e}")
        return None


def _apply_gain_control(block: np.ndarray, cfg: config.VAD):
    rms = np.sqrt(np.mean(block**2))
    if rms < cfg.NOISE_FLOOR_RMS:
        return block
    gain = cfg.TARGET_RMS / rms
    gain = min(gain, 10 ** (cfg.MAX_GAIN_DB / 20))
    return np.clip(block * gain, -1.0, 1.0)


def median_filter(x, k):
    if k % 2 == 0:
        k += 1
    pad_len = k // 2
    x_padded = np.pad(x, (pad_len, pad_len), mode="reflect")
    return np.array([np.median(x_padded[i : i + k]) for i in range(len(x))])


def worker(
    vad_model,
    audio_q: queue.Queue,
    chunk_q: queue.Queue,
    dict_control,
    vad_event_q: queue.Queue,
    shutdown_event: threading.Event,
):
    log.info("VAD worker started with new non-overlapping chunking logic.")
    cfg = config.VAD
    log.info(
        f"CONFIG: CHUNK_MS={cfg.CHUNK_DURATION_MS}, STEP_MS={cfg.STEP_DURATION_MS}, ON_THRESH={cfg.ON_THRESHOLD}, OFF_THRESH={cfg.OFF_THRESHOLD}"
    )

    chunk_size = config.Audio.SAMPLE_RATE * cfg.CHUNK_DURATION_MS // 1000
    step_size = config.Audio.SAMPLE_RATE * cfg.STEP_DURATION_MS // 1000

    state = VadState.SILENCE
    shared_vad_state.set("SILENCE")
    buffer = np.empty(0, dtype=np.float32)
    speech_buffer = []
    device = "cuda" if torch.cuda.is_available() else "cpu"
    flush_counter = 0
    frames_since_speech = 0

    while not shutdown_event.is_set():
        try:
            block = audio_q.get(timeout=0.1).flatten()
            buffer = np.concatenate([buffer, block])
        except queue.Empty:
            continue

        while len(buffer) >= chunk_size:
            # Analyze a large chunk for context
            chunk_to_analyze = buffer[:chunk_size]

            # --- The actual audio slice we will buffer is just the new part ---
            new_audio_slice = buffer[:step_size]
            buffer = buffer[step_size:]  # Advance the buffer

            gained_chunk = _apply_gain_control(chunk_to_analyze, cfg)

            with torch.no_grad():
                tensor = (
                    torch.tensor(gained_chunk, dtype=torch.float32)
                    .unsqueeze(0)
                    .to(device)
                )
                logits = vad_model(
                    input_signal=tensor,
                    input_signal_length=torch.tensor([len(gained_chunk)]).to(device),
                )
                probs = torch.sigmoid(logits[0, :, 1]).cpu().numpy()

            smoothed_probs = median_filter(probs, cfg.SMOOTHING_WINDOW_FRAMES)

            # Make a single decision for the new slice based on the analysis of the whole chunk
            is_speech = np.mean(smoothed_probs) >= cfg.ON_THRESHOLD

            if state == VadState.SILENCE:
                if is_speech:
                    log.info("VAD activation confirmed.")
                    vad_event_q.put(None)
                    state = VadState.SPEECH
                    shared_vad_state.set("SPEECH")
                    speech_buffer.append(new_audio_slice)
            elif state == VadState.SPEECH:
                if is_speech:
                    frames_since_speech = 0
                    speech_buffer.append(new_audio_slice)
                else:
                    frames_since_speech += 1
                    # DEACTIVATION_FRAMES is in 20ms frames, our step is 80ms.
                    deactivation_steps = (
                        cfg.DEACTIVATION_FRAMES * 20
                    ) / cfg.STEP_DURATION_MS
                    if frames_since_speech >= deactivation_steps:
                        flush_counter = _flush_chunk(
                            speech_buffer, chunk_q, dict_control, flush_counter
                        )
                        state = VadState.SILENCE
                        shared_vad_state.set("SILENCE")
                        speech_buffer = []
                    else:
                        speech_buffer.append(
                            new_audio_slice
                        )  # Buffer the silence as a natural pause
    log.info("VAD worker shutting down.")


def _flush_chunk(buffer, chunk_q, dict_control, counter):
    if not buffer:
        return counter

    # if not dict_control.is_active():
    # log.info("Dictation inactive, dropping chunk.")
    # return counter
    try:
        full_chunk = np.concatenate(buffer)
        log.info(
            f"Flushing {len(full_chunk) / config.Audio.SAMPLE_RATE:.2f}s audio chunk."
        )
        chunk_q.put(full_chunk)


        # if config.VAD.DEBUG_SAVE_WAVS:
            # debug_dir = config.PROJECT_ROOT / "vad_debug"
            # debug_dir.mkdir(exist_ok=True)
            # sf.write(
                # debug_dir / f"flushed_chunk_{counter}.wav",
                # full_chunk,
                # config.Audio.SAMPLE_RATE,
            # )
        return counter + 1
    except Exception as e:
        log.error(f"Error during chunk flush: {e}")
        return counter
