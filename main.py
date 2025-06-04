#!/usr/bin/env python3

import os
import sys
import queue
import threading
import argparse
import subprocess
import time
import socket
import atexit
import shutil
import uuid
import signal
import tempfile
from pathlib import Path

import pyperclip
try:
    import keyboard
except Exception:  # noqa: E722
    keyboard = None
try:
    import pyautogui
except Exception:  # noqa: E722
    pyautogui = None
try:
    from win10toast import ToastNotifier
except Exception:  # noqa: E722
    ToastNotifier = None
try:
    from plyer import notification as plyer_notification
except Exception:  # noqa: E722
    plyer_notification = None

import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write as wav_write

import webrtcvad
import nemo.collections.asr as nemo_asr

# ========== CONSTANTS ==========
TEMP_DIR = tempfile.gettempdir()
SOCK_PATH = os.environ.get("STT_SOCK_PATH", os.path.join(TEMP_DIR, "sttdict.sock"))
TCP_PORT = int(os.environ.get("STT_PORT", "8765"))
STATE_PATH = Path(TEMP_DIR) / "sttdict.state"
VOLUME_THRESHOLD = 0.01  # Adjust to filter silence
VAD_SENSITIVITY = 2  # 0=least, 3=most sensitive
SAMPLE_RATE = 16000  # Parakeet expects 16kHz mono audio
MAX_AUDIO_BUFFER_SEC = 30  # Max seconds of speech buffer before forced flush

IS_WINDOWS = sys.platform.startswith("win")

# ========== GLOBALS ==========
args = None
dict_control = None
shutdown_event = threading.Event()  # For graceful shutdown

# ========== UTILITY FUNCTIONS ==========


def warn_x11():
    if IS_WINDOWS:
        return
    xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
    display = os.environ.get("DISPLAY", "")
    if xdg_session != "x11" or not display:
        print(
            "[WARNING] You are not in a graphical X11 session. Typing/paste automation will NOT work."
        )
        print(
            "Open a GUI terminal (like Gnome Terminal, Konsole, etc) inside your desktop and try again."
        )


def can_xdotool():
    return bool(shutil.which("xdotool")) and bool(os.environ.get("DISPLAY"))


def try_notify(msg):
    global args
    if args is not None and hasattr(args, "no_notify") and args.no_notify:
        return
    if not IS_WINDOWS and shutil.which("notify-send") and os.environ.get("DISPLAY"):
        try:
            subprocess.run(["notify-send", msg])
            return
        except Exception as e:
            print(f"[notify-send ERROR] {e}", file=sys.stderr)
    elif IS_WINDOWS:
        if ToastNotifier is not None:
            try:
                # Using threaded=False avoids issues with the background
                # window procedure on newer Python/Windows versions.
                ToastNotifier().show_toast(
                    "STT", msg, threaded=False, duration=3
                )
                return
            except Exception as e:
                print(f"[win10toast ERROR] {e}", file=sys.stderr)
        if plyer_notification is not None:
            try:
                plyer_notification.notify(title="STT", message=msg)
                return
            except Exception as e:
                print(f"[plyer notification ERROR] {e}", file=sys.stderr)


def cleanup(sock_path=SOCK_PATH):
    try:
        if os.path.exists(sock_path):
            os.remove(sock_path)
    except Exception as e:
        print(f"[CLEANUP ERROR]: {e}", file=sys.stderr)


atexit.register(cleanup)

# ========== DICTATION CONTROL ==========


class DictationControl:
    def __init__(self):
        self.active = False
        self.lock = threading.Lock()
        # Write an initial “off” state on startup
        self._write_state()

    def _write_state(self):
        try:
            with open(STATE_PATH, "w") as f:
                f.write("on" if self.active else "off")
        except Exception:
            pass

    def toggle(self):
        with self.lock:
            self.active = not self.active
            self._write_state()  # ← write state here
            return self.active

    def stop(self):
        with self.lock:
            was_active = self.active
            self.active = False
            self._write_state()  # ← and here
            return was_active

    def is_active(self):
        with self.lock:
            return self.active


dict_control = DictationControl()


def socket_listener(sock_path=SOCK_PATH, port=TCP_PORT):
    cleanup(sock_path)
    if hasattr(socket, "AF_UNIX"):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(sock_path)
            try:
                os.chmod(sock_path, 0o600)
            except (NotImplementedError, PermissionError):
                pass
        except OSError as e:
            print(f"[Socket error]: {e}")
            print(f"Try deleting {sock_path} if it exists and re-run.")
            sys.exit(1)
        bind_desc = f"unix:{sock_path}"
    else:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", port))
        except OSError as e:
            print(f"[Socket error]: {e}")
            print(f"Is port {port} already in use?")
            sys.exit(1)
        bind_desc = f"tcp://127.0.0.1:{port}"
    server.listen(1)
    server.settimeout(1.0)
    print(f"[Dictation control socket at {bind_desc}]")

    def loop():
        while not shutdown_event.is_set():
            try:
                conn, _ = server.accept()
                cmd = conn.recv(128).decode(errors="ignore").strip()
                if cmd == "toggle":
                    state = dict_control.toggle()
                    if state:
                        print("[Dictation started!]")
                        try_notify("Dictation started!")
                    else:
                        print("[Dictation stopped!]")
                        try_notify("Dictation stopped!")
                elif cmd == "stop":
                    if dict_control.stop():
                        print("[Dictation stopped!]")
                        try_notify("Dictation stopped!")
                conn.close()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[Socket listener error]: {e}", file=sys.stderr)
        server.close()
        cleanup(sock_path)

    threading.Thread(target=loop, daemon=True).start()


# ========== TEXT TYPING ==========


def type_text(txt, ascii_only=False):
    if ascii_only:
        filtered_txt = txt.encode("ascii", errors="ignore").decode()
        if filtered_txt != txt:
            print("[INFO] Some Unicode characters were omitted (ASCII-only mode).")
        txt = filtered_txt

    typed = False
    if not IS_WINDOWS and can_xdotool():
        time.sleep(0.2)
        print(f"[Typed]: {txt}")
        try:
            subprocess.run(["xdotool", "type", "--delay", "0", txt])
            typed = True
        except Exception as e:
            print(f"[ERROR] xdotool typing failed: {e}")

    if not typed:
        typed = fallback_typing(txt)

    if not typed:
        print(f"[FAKE TYPE] Would have typed: {txt}")
        try_clipboard(txt)


def fallback_typing(txt):
    if keyboard is not None:
        try:
            keyboard.write(txt)
            print(f"[Typed via keyboard]: {txt}")
            return True
        except Exception as e:
            print(f"[keyboard ERROR]: {e}")
    if pyautogui is not None:
        try:
            pyautogui.typewrite(txt)
            print(f"[Typed via pyautogui]: {txt}")
            return True
        except Exception as e:
            print(f"[pyautogui ERROR]: {e}")
    return False


def try_clipboard(txt):
    try:
        pyperclip.copy(txt)
        print("[Clipboard]: Text copied to clipboard.")
    except Exception as e:
        print(f"[Clipboard ERROR]: {e}", file=sys.stderr)


# ========== STT MODEL SETUP ==========


def load_parakeet_model():
    print("[INFO] Loading Parakeet TDT model...")
    try:
        model = nemo_asr.models.ASRModel.from_pretrained(
            model_name="nvidia/parakeet-tdt-0.6b-v2"
        )
        return model
    except Exception as e:
        print(f"[Error loading Parakeet model]: {e}")
        print(
            "Check GPU availability, internet connection, or try a different ASR model."
        )
        sys.exit(1)


def recognize_parakeet(asr_model, audio_array):
    tmp_dir = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_dir, f"{uuid.uuid4()}.wav")
    try:
        wav_write(tmp_path, SAMPLE_RATE, audio_array)
    except Exception as e:
        print(f"[Error writing temp WAV]: {e}", file=sys.stderr)
        return ""
    try:
        result = asr_model.transcribe([tmp_path])
        return result[0]
    except Exception as e:
        print(f"[ASR error]: {e}", file=sys.stderr)
        return ""
    finally:
        try:
            os.remove(tmp_path)
        except Exception as e:
            print(f"[Tempfile cleanup error]: {e}", file=sys.stderr)


# ========== STT WORKER THREAD ==========

vad = webrtcvad.Vad(VAD_SENSITIVITY)


def stt_worker(asr_model, audio_q, ascii_only):
    VAD_CHUNK_MS = 30  # ms per chunk
    MAX_SILENCE_MS = 600
    max_silence_chunks = MAX_SILENCE_MS // VAD_CHUNK_MS
    max_audio_buffer_len = SAMPLE_RATE * MAX_AUDIO_BUFFER_SEC

    audio_buffer = []
    silence_chunks = 0

    while not shutdown_event.is_set():
        try:
            audio = audio_q.get(timeout=0.5)
        except queue.Empty:
            continue
        audio_array = np.array(audio)
        if audio_array.ndim > 1:
            audio_array = audio_array.mean(axis=1)
        audio_array = audio_array.flatten()
        # No resampling needed! Everything is at 16kHz.
        audio_int16 = (audio_array * 32767).astype(np.int16)
        # VAD works on 10/20/30 ms windows at 16kHz
        vad_win_size = int(0.03 * SAMPLE_RATE)  # 30 ms
        vad_bytes = audio_int16[:vad_win_size].tobytes()
        try:
            is_speech = vad.is_speech(vad_bytes, sample_rate=SAMPLE_RATE)
        except Exception as e:
            print(f"[VAD error]: {e}", file=sys.stderr)
            is_speech = False

        if is_speech:
            audio_buffer.append(audio_int16)
            silence_chunks = 0
            total_len = sum(len(chunk) for chunk in audio_buffer)
            if total_len > max_audio_buffer_len:
                print("[WARNING] Audio buffer exceeded max length, flushing early.")
                flush_audio_buffer(asr_model, audio_buffer, ascii_only)
                audio_buffer.clear()
                silence_chunks = 0
        elif audio_buffer:
            silence_chunks += 1
            if silence_chunks >= max_silence_chunks:
                flush_audio_buffer(asr_model, audio_buffer, ascii_only)
                audio_buffer.clear()
                silence_chunks = 0
        # If no speech and no buffer, do nothing


def flush_audio_buffer(asr_model, audio_buffer, ascii_only):
    if dict_control.is_active():
        try:
            full_audio = np.concatenate(audio_buffer)
        except Exception as e:
            print(f"[Buffer concatenate error]: {e}", file=sys.stderr)
            return
        text = recognize_parakeet(asr_model, full_audio)
        if isinstance(text, dict) and "text" in text:
            out_text = text["text"]
        elif hasattr(text, "text"):
            out_text = text.text
        else:
            out_text = str(text)
        if out_text.strip():
            type_text(out_text + " ", ascii_only=ascii_only)
        else:
            print("[INFO] ASR returned empty output.")
    else:
        print("[INFO] Dictation is not active, audio discarded.")


# ========== STATUS THREAD ==========


def print_status():
    was_active = None
    while not shutdown_event.is_set():
        active = dict_control.is_active()
        if active != was_active:
            print("[Dictation mode ON]" if active else "[Dictation mode OFF]")
            was_active = active
        time.sleep(0.5)


# ========== TROUBLESHOOTING ==========


def print_troubleshooting():
    default_sock = os.path.join(TEMP_DIR, "sttdict.sock")
    print(
        f"""TROUBLESHOOTING

If no text is typed:
  - Is your session X11?   (echo $XDG_SESSION_TYPE; should print x11)
  - Is xdotool installed?  (run 'xdotool --version')
  - Is the cursor in a writable text field in a GUI app?
  - Is your microphone device index correct?
  - Are you seeing any errors above?
  - For Unicode/emoji issues, try --ascii-only.

Wayland users:
  - xdotool does not work under Wayland. Try using ydotool (requires setup).
  - Or, log into an X11 session.

If 'Dictation started!'/notifications appear, but nothing types:
  - Your desktop may have grabbed focus (disable notifications with --no-notify).
  - Some rare apps block simulated typing (try gedit/leafpad for testing).

If you see socket errors:
  - Remove {default_sock} if it exists and restart the script.

If audio input fails:
  - Run with --list-devices and set --device <index>.
"""
    )


# ========== MAIN ==========


def main():
    global args
    warn_x11()
    parser = argparse.ArgumentParser(
        description="Live Dictation (Speech-to-Type) with robust error handling"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Mic input device index (see --list-devices)",
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="List audio devices and exit"
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="Disable desktop notifications"
    )
    parser.add_argument(
        "--ascii-only", action="store_true", help="Only type ASCII text (drop Unicode)"
    )
    parser.add_argument(
        "--help-me", action="store_true", help="Show troubleshooting info and exit"
    )
    args = parser.parse_args()

    if args.help_me:
        print_troubleshooting()
        return

    if args.list_devices:
        print("\nAvailable audio devices:\n")
        print(sd.query_devices())
        print("\nTo select a device, use: --device <index>\n")
        return

    try:
        asr_model = load_parakeet_model()
    except Exception as e:
        print(f"[Error loading Parakeet model]: {e}", file=sys.stderr)
        return

    audio_q = queue.Queue()

    def audio_callback(indata, frames, time_, status):
        # Ensure always shape (N, 1)
        if indata.ndim > 1 and indata.shape[1] > 1:
            indata = indata.mean(axis=1, keepdims=True)
        audio_q.put(indata.copy())

    socket_listener()
    threading.Thread(target=print_status, daemon=True).start()

    def signal_handler(sig, frame):
        print("\n[INFO] Exiting, cleaning up...")
        shutdown_event.set()
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        stream = sd.InputStream(
            callback=audio_callback,
            channels=1,
            samplerate=16000,  # Always 16kHz!
            dtype="float32",
            blocksize=480,  # 30 ms
            device=args.device,
        )
        stream.start()
    except Exception as e:
        print(f"[Error starting audio stream]: {e}", file=sys.stderr)
        print("\nAvailable audio devices:")
        print(sd.query_devices())
        print("Set device with --device <index>")
        return

    threading.Thread(
        target=stt_worker,
        args=(asr_model, audio_q, args.ascii_only),
        daemon=True,
    ).start()

    print(
        "Ready! Press your hotkey (super+alt+v) to toggle live dictation.\nFor troubleshooting, run with --help-me."
    )
    while not shutdown_event.is_set():
        time.sleep(1)
    print("[INFO] Main thread exiting.")


if __name__ == "__main__":
    main()
