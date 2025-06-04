import threading
import socket
from PIL import Image, ImageDraw
import pystray
import main


def _create_image():
    size = 64
    image = Image.new('RGB', (size, size), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((16, 16, 48, 48), fill='black')
    return image


def _toggle(icon=None, item=None):
    """Send a toggle command to the running STT instance."""
    if hasattr(socket, "AF_UNIX"):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connect_addr = main.SOCK_PATH
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connect_addr = ("127.0.0.1", main.TCP_PORT)
    try:
        sock.connect(connect_addr)
        sock.sendall(b"toggle")
    except Exception as e:
        print(f"[Tray toggle error]: {e}")
    finally:
        sock.close()


def _exit(icon, item):
    main.shutdown_event.set()
    icon.stop()


def main_tray():
    threading.Thread(target=main.main, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem('Toggle dictation', _toggle, default=True),
        pystray.MenuItem('Exit', _exit)
    )
    icon = pystray.Icon('stt', _create_image(), 'STT', menu)
    icon.run()


if __name__ == '__main__':
    main_tray()
