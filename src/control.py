import os
import sys
import threading
import socket
import atexit
import subprocess

TEMP_DIR = os.environ.get("TMPDIR", "/tmp")
SOCK_PATH = os.path.join(TEMP_DIR, "sttdict.sock")

# Notification helper
try:
    from plyer import notification
except ImportError:
    notification = None


def notify(title, message):
    # Prefer plyer for cross-platform, fallback to notify-send on Linux
    notified = False
    if notification:
        try:
            notification.notify(title=title, message=message, timeout=2)
            notified = True
        except Exception:
            pass
    if not notified:
        try:
            subprocess.run(["notify-send", title, message])
            notified = True
        except Exception:
            pass
    # Could add more OS support (win10toast etc) if you want


class DictationControl:
    def __init__(self, state_path=None):
        self.active = False
        self.lock = threading.Lock()
        self.state_path = state_path
        self._write_state()

    def _write_state(self):
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w") as f:
                f.write("on" if self.active else "off")
        except Exception:
            pass

    def toggle(self):
        with self.lock:
            self.active = not self.active
            self._write_state()
            return self.active

    def stop(self):
        with self.lock:
            was_active = self.active
            self.active = False
            self._write_state()
            return was_active

    def is_active(self):
        with self.lock:
            return self.active


def cleanup(sock_path=SOCK_PATH):
    try:
        if os.path.exists(sock_path):
            os.remove(sock_path)
    except Exception as e:
        print(f"[CLEANUP ERROR]: {e}", file=sys.stderr)


atexit.register(cleanup)


def socket_listener(dict_control, shutdown_event, sock_path=SOCK_PATH):
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
            server.bind(("127.0.0.1", 8765))
        except OSError as e:
            print(f"[Socket error]: {e}")
            print("Is port 8765 already in use?")
            sys.exit(1)
        bind_desc = "tcp://127.0.0.1:8765"
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
                    print("[Dictation started!]" if state else "[Dictation stopped!]")
                    notify("Dictation", "Started" if state else "Stopped")
                elif cmd == "stop":
                    if dict_control.stop():
                        print("[Dictation stopped!]")
                        notify("Dictation", "Stopped")
                conn.close()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[Socket listener error]: {e}", file=sys.stderr)
        server.close()
        cleanup(sock_path)

    threading.Thread(target=loop, daemon=True).start()
