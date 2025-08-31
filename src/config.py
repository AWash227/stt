# src/config.py
"""
Centralized configuration for the entire STT application.
This file acts as the single source of truth for all settings.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
MODEL_DIR = PROJECT_ROOT


class Audio:
    SAMPLE_RATE = 16000
    INPUT_DEVICE_INDEX = None


class Aggregator:
    # This is the key setting: how long of a pause (in seconds)
    # between utterances triggers a transcription.
    FLUSH_TIMEOUT_S = 1.5

    # A safety valve to prevent infinitely long buffers.
    MAX_BUFFER_S = 30.0


class VAD:
    MODEL_NAME = "nvidia/frame_vad_multilingual_marblenet_v2.0"
    MAX_AUDIO_BUFFER_SEC = 20
    DEBUG_SAVE_WAVS = True
    VERBOSE_DEBUG = False
    # Use chunked processing for better context.
    CHUNK_DURATION_MS = 640  # How much audio to feed the model at once.
    STEP_DURATION_MS = 80  # How much to advance the window each time.

    # --- FINAL ARCHITECTURE: Chunked Processing ---
    # The VAD will process audio in larger, overlapping chunks.
    # This gives the model the temporal context it needs to be accurate.
    # A chunk of 400-800ms is recommended.
    CHUNK_DURATION_MS = 640  # Process audio in 640ms chunks.
    # How often to run inference. A smaller step means lower latency but more CPU/GPU usage.
    STEP_DURATION_MS = 80

    # --- Smart AGC with Noise Gate ---
    NOISE_FLOOR_RMS = 0.007
    TARGET_RMS = 0.09
    MAX_GAIN_DB = 20.0

    # --- Smoothing and Hysteresis Parameters (applied to each chunk's probabilities) ---
    SMOOTHING_WINDOW_FRAMES = 5
    ON_THRESHOLD = 0.45
    OFF_THRESHOLD = 0.35
    ACTIVATION_FRAMES = 3
    DEACTIVATION_FRAMES = 20  # 1 second of silence to end an utterance.


class ASR:
    MODEL_PATH = str(MODEL_DIR / "parakeet-tdt-0.6b-v3.nemo")


class LLM:
    ENABLED = False
    URL = "http://localhost:11434"
    MODEL = "qwen3:0.6b"
    PROMPT_PATH = Path(__file__).parent / "prompts" / "postprocess.md"
    TIMEOUT_S = 15
    MAX_RETRIES = 2
    LOG_INTERACTIONS = True


class Display:
    ENABLED = True


class Output:
    TYPE_OUTPUT = True
