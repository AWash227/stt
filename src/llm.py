# src/llm.py
import queue, threading, requests, logging
from config import LLM as Cfg
from log import log_interaction

log = logging.getLogger(__name__)

try:
    with open(Cfg.PROMPT_PATH, "r", encoding="utf-8") as f:
        SYSTEM_PROMPT = f.read()
    log.info(f"Loaded system prompt from {Cfg.PROMPT_PATH}")
except FileNotFoundError:
    log.error(f"CRITICAL: Prompt file not found at {Cfg.PROMPT_PATH}")
    SYSTEM_PROMPT = "You are a helpful assistant."


def post_process_text(text: str) -> str:
    if not text.strip():
        return ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    for attempt in range(Cfg.MAX_RETRIES):
        try:
            response = requests.post(
                f"{Cfg.URL}/api/chat",
                json={
                    "model": Cfg.MODEL,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0.0},
                },
                timeout=Cfg.TIMEOUT_S,
            )
            response.raise_for_status()
            data = response.json()
            output = data.get("message", {}).get("content", "")
            if Cfg.LOG_INTERACTIONS:
                log_interaction(text, output)
            return output.strip()
        except requests.exceptions.RequestException as e:
            if "404" in str(e):
                log.error(
                    f"LLM model '{Cfg.MODEL}' not found. Please run `ollama pull {Cfg.MODEL}`."
                )
                break  # Don't retry on a 404
            log.warning(f"API call failed (attempt {attempt+1}/{Cfg.MAX_RETRIES}): {e}")
    if Cfg.LOG_INTERACTIONS:
        log_interaction(text, f"PASSTHROUGH: {text}")
    return text


def worker(
    input_q: queue.Queue,
    output_q: queue.Queue,
    pipeline_event_q: queue.Queue,
    shutdown_event: threading.Event,
):
    log.info("LLM worker started.")
    while not shutdown_event.is_set():
        try:
            raw_text = input_q.get(timeout=0.5)
        except queue.Empty:
            continue

        pipeline_event_q.put("llm_start")
        processed_text = post_process_text(raw_text)
        if processed_text:
            output_q.put(processed_text)
    log.info("LLM worker shutting down.")
