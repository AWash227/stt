"""
src/mic.py
──────────
• Captures mono float-32 audio via sounddevice
• Resamples to 16 kHz if needed (scipy polyphase)
• Applies RMS automatic-gain-control (-20 LUFS target)
• 120 Hz high-pass + DC-block to remove rumble
• Sends 30 ms blocks to `audio_q` (and `monitor_q` if present)
"""

from __future__ import annotations
import queue, threading, logging, time

import numpy as np
import sounddevice as sd
import scipy.signal

import config

log = logging.getLogger(__name__)

TARGET_SR = config.Audio.SAMPLE_RATE  # 16 000
FRAME_MS = 30  # 30 ms → 480 samples @16 k
HPF_CUTOFF = 120  # Hz
AGC_TARGET = 0.09  # ≈-20 LUFS
AGC_MAX_DB = 20.0  # safety cap


# ────────────────────────────────────────
#  Simple DSP helpers
# ────────────────────────────────────────
def _apply_agc(
    block: np.ndarray, target_rms=AGC_TARGET, max_gain_db=AGC_MAX_DB
) -> np.ndarray:
    rms = np.sqrt(np.mean(block**2) + 1e-9)
    gain = min(target_rms / rms, 10 ** (max_gain_db / 20))
    return np.clip(block * gain, -1.0, 1.0).astype(np.float32)


def _highpass(block: np.ndarray, sr: int, cutoff: int = HPF_CUTOFF) -> np.ndarray:
    block = block - np.mean(block)  # DC-block
    b, a = scipy.signal.butter(2, cutoff / (0.5 * sr), "high")
    return scipy.signal.lfilter(b, a, block).astype(np.float32)


# ────────────────────────────────────────
#  PortAudio callback factory
# ────────────────────────────────────────
def _make_callback(sr_in: int, audio_q: queue.Queue, monitor_q: queue.Queue | None):

    def cb(indata, frames, time_info, status):
        if status:
            log.warning(f"[mic] {status}")

        block = np.mean(indata, axis=1).astype(np.float32)  # mono

        # resample to 16 k if device SR differs
        if sr_in != TARGET_SR:
            new_len = int(len(block) * TARGET_SR / sr_in)
            block = scipy.signal.resample_poly(block, TARGET_SR, sr_in).astype(
                np.float32
            )

        block = _apply_agc(block)
        block = _highpass(block, TARGET_SR)

        audio_q.put(block)
        if monitor_q is not None:
            monitor_q.put(block)

    return cb


# ────────────────────────────────────────
#  Worker thread
# ────────────────────────────────────────
def worker(
    audio_q: queue.Queue,
    monitor_q: queue.Queue,
    shutdown_event: threading.Event,
    control_q: queue.Queue | None = None,
):

    def pick_initial_device():
        try:
            if config.Audio.INPUT_DEVICE_INDEX is not None:
                return int(config.Audio.INPUT_DEVICE_INDEX)
        except Exception:
            pass
        try:
            # default input index
            return sd.default.device[0]
        except Exception:
            return None

    current_dev = pick_initial_device()

    while not shutdown_event.is_set():
        try:
            if current_dev is None:
                # pick any valid input device
                devs = sd.query_devices()
                input_idxs = [i for i, d in enumerate(devs) if d.get("max_input_channels", 0) > 0]
                current_dev = input_idxs[0] if input_idxs else None
                if current_dev is None:
                    raise RuntimeError("No input devices available")

            info = sd.query_devices(current_dev, "input")
            sr_in = int(info["default_samplerate"])
            block = int(sr_in * FRAME_MS / 1000)

            stream = sd.InputStream(
                device=current_dev,
                channels=1,
                samplerate=sr_in,
                dtype="float32",
                blocksize=block,
                callback=_make_callback(sr_in, audio_q, monitor_q),
            )

            with stream:
                log.info(
                    f"[mic] Device '{info['name']}' – {sr_in} Hz "
                    f"(→ {TARGET_SR} Hz), {FRAME_MS} ms frames"
                )
                # Poll for control messages or shutdown
                while not shutdown_event.is_set():
                    # Handle device switch commands
                    if control_q is not None:
                        try:
                            cmd, payload = control_q.get(timeout=0.2)
                            if cmd == "set_device_index":
                                try:
                                    new_idx = int(payload)
                                    if new_idx != current_dev:
                                        log.info(f"[mic] Switching device → index {new_idx}")
                                        current_dev = new_idx
                                        break  # exit stream and reopen
                                except Exception:
                                    log.warning(f"[mic] Invalid device index: {payload}")
                            elif cmd == "set_device_name":
                                # Try resolve by name substring
                                try:
                                    devs = sd.query_devices()
                                    match = None
                                    for i, d in enumerate(devs):
                                        if d.get("max_input_channels", 0) > 0 and payload.lower() in d.get("name", "").lower():
                                            match = i
                                            break
                                    if match is not None and match != current_dev:
                                        log.info(f"[mic] Switching device → '{devs[match]['name']}' (#{match})")
                                        current_dev = match
                                        break
                                except Exception:
                                    log.warning(f"[mic] Could not match name: {payload}")
                        except queue.Empty:
                            pass
                    time.sleep(0.02)
        except Exception as e:
            log.error(f"[mic] Fatal: {e}", exc_info=True)
            time.sleep(0.5)
            # retry
            continue

    shutdown_event.set()
