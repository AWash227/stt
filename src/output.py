# src/output.py
import queue, logging, subprocess, time, sys, threading, json
import config
from control import DictationControl

log = logging.getLogger(__name__)

try:
    import pyperclip
except ImportError:
    pyperclip = None
try:
    import keyboard
except ImportError:
    keyboard = None
try:
    import pyautogui
except ImportError:
    pyautogui = None


def can_xdotool():
    import shutil, os

    return bool(shutil.which("xdotool")) and bool(os.environ.get("DISPLAY"))


def type_text(text: str):
    text = text + " "
    log.info(f"Attempting to type: '{text}'")
    if can_xdotool():
        try:
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--delay", "0", text],
                check=True,
            )
            return
        except Exception as e:
            log.warning(f"xdotool failed: {e}. Trying next method.")
    if pyautogui:
        try:
            pyautogui.write(text, interval=0)
            return
        except Exception as e:
            log.warning(f"pyautogui failed: {e}. Trying next method.")
    if keyboard:
        try:
            keyboard.write(text)
            return
        except Exception as e:
            log.warning(f"keyboard library failed: {e}. Trying next method.")
    if pyperclip:
        try:
            pyperclip.copy(text)
            log.info("No typing method worked. Text copied to clipboard instead.")
            return
        except Exception as e:
            log.error(f"All typing methods and clipboard copy failed: {e}")
    log.error("All output methods failed. Text could not be typed or copied.")


def worker(
    text_queue: queue.Queue,
    pipeline_event_q: queue.Queue,
    dict_control: DictationControl,
    shutdown_event: threading.Event,
):
    log.info("Output worker started.")
    with open("narration.jsonl", "a", encoding="utf-8") as f:
        while not shutdown_event.is_set():
            try:
                text = text_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            pipeline_event_q.put("output_start")
            print(f"Final Text >>> {text}")
            if config.Output.TYPE_OUTPUT and text and dict_control.is_active():
                type_text(text)

            # LOG TO FILE FOR LLM FEEDBACK
            entry = {"timestamp": time.time(), "text": text}
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
    log.info("Output worker shutting down.")
